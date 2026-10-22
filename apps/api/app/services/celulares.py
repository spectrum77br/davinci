"""Ponte restrita do piloto de celulares; o token do agente fica neste servidor.

Não cria aparelhos, não altera proxies e não encaminha caminhos arbitrários.
O agente só mede se a porta TCP do proxy responde: isso NÃO confirma internet.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
import structlog
from pydantic import BaseModel, Field

from app.config import get_settings

logger = structlog.get_logger()
NAME_RE = re.compile(r"[a-z][a-z0-9]{1,19}\Z")
MAX_NAMES = 100
MAX_RESPONSE_BYTES = 512 * 1024
TOTAL_TIMEOUT_S = 15.0
Action = Literal["start", "stop", "restart"]
ACTIONS = frozenset({"start", "stop", "restart"})


class CelularesError(Exception):
    """Erro público com texto fixo: nunca repassa corpo/URL/segredos do agente."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


def _invalid_config() -> CelularesError:
    return CelularesError(
        503, "celulares_configuracao_invalida",
        "A conexão dos celulares precisa ser revisada pelo administrador.",
    )


def _unavailable() -> CelularesError:
    return CelularesError(
        503, "celulares_indisponiveis",
        "O servidor dos celulares não está disponível. Tente novamente em alguns instantes.",
    )


def _invalid_response() -> CelularesError:
    return CelularesError(
        502, "celulares_resposta_invalida",
        "Não foi possível confirmar o estado dos celulares. Atualize antes de tentar novamente.",
    )


def _not_found() -> CelularesError:
    return CelularesError(
        404, "celular_nao_liberado", "Este celular não está disponível neste piloto.",
    )


@dataclass(frozen=True)
class AgentConfig:
    api_url: str
    token: str = field(repr=False)
    allowed_names: frozenset[str]
    socket_path: str | None = None


class NetworkOut(BaseModel):
    egress: Literal["proxy", "blocked", "direct", "unknown"]
    proxy_configured: bool
    proxy_tcp_reachable: bool | None
    # O agente atual não executa uma prova de internet autenticada pelo proxy.
    internet_confirmed: None = None


class IsolationOut(BaseModel):
    celguard: bool | None
    bridge_ipv6_off: bool | None
    dns_proprio: bool | None


class PhoneOut(BaseModel):
    name: str
    read_only: bool = False
    camera_test: bool = False
    camera_available: bool = False
    running: bool
    boot_completed: bool
    desired: Literal["running", "stopped", "removed"] | None
    sidecar_running: bool | None
    network: NetworkOut
    isolation: IsolationOut
    memory_mb: float | None
    cpu_percent: float | None
    has_error: bool


class PhonesOut(BaseModel):
    stream_enabled: bool = False
    enabled: bool
    phones: list[PhoneOut] = Field(default_factory=list)


class ActionOut(BaseModel):
    action: Action
    phone: PhoneOut


def _safe_api_url(raw: str) -> str:
    if not raw.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in raw):
        raise _invalid_config()
    try:
        url = urlsplit(raw)
        port = url.port
    except ValueError:
        raise _invalid_config() from None
    if (
        url.scheme not in {"https", "http"}
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
        or port == 0
        or "\\" in raw
    ):
        raise _invalid_config()
    if url.scheme == "http":
        host = url.hostname.lower()
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if not local:
            raise _invalid_config()
    try:
        normalized = httpx.URL(raw)
    except httpx.InvalidURL:
        raise _invalid_config() from None
    return str(normalized).rstrip("/")


def configuracao() -> AgentConfig | None:
    """Ausência de qualquer configuração desliga o piloto, sem tentar a rede."""
    settings = get_settings()
    raw_url = getattr(settings, "celulares_api_url", "")
    raw_token = getattr(settings, "celulares_admin_token", "")
    raw_names = getattr(settings, "celulares_allowlist", "")
    token = raw_token.get_secret_value() if hasattr(raw_token, "get_secret_value") else raw_token
    if not raw_url or not token or not raw_names:
        return None
    if not isinstance(raw_url, str) or not isinstance(token, str):
        raise _invalid_config()
    if any(ord(char) < 32 or ord(char) == 127 for char in raw_url):
        raise _invalid_config()
    raw_url, token = raw_url.strip(), token.strip()
    if isinstance(raw_names, str):
        names = [name.strip() for name in raw_names.split(",") if name.strip()]
    elif isinstance(raw_names, list | tuple | set | frozenset):
        names = list(raw_names)
    else:
        raise _invalid_config()
    if not raw_url or not token or not names:
        return None
    if (
        len(token) < 24
        or len(token) > 4096
        or not all(33 <= ord(char) <= 126 for char in token)
        or len(names) > MAX_NAMES
        or any(not isinstance(name, str) or not NAME_RE.fullmatch(name) for name in names)
    ):
        raise _invalid_config()
    api_url = _safe_api_url(raw_url)
    socket_path = getattr(settings, "celulares_api_socket", "")
    # The private SSH tunnel is mounted read-only into the API container. Never
    # fall back to TCP if this socket disappears or the tunnel disconnects.
    if socket_path and (
        socket_path != "/run/davinci-celulares/agent.sock"
        or api_url != "http://localhost"
    ):
        raise _invalid_config()
    return AgentConfig(api_url, token, frozenset(names), socket_path or None)


def _required_config() -> AgentConfig:
    config = configuracao()
    if config is None:
        raise CelularesError(
            503, "celulares_desativados", "O piloto de celulares ainda não foi habilitado.",
        )
    return config


def _client(config: AgentConfig | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=config.socket_path, trust_env=False)
        if config and config.socket_path else None,
        timeout=httpx.Timeout(5.0, connect=3.0),
        follow_redirects=False,
        trust_env=False,
    )


async def _request(config: AgentConfig, method: str, path: str) -> Any:
    headers = {"Authorization": f"Bearer {config.token}", "Accept": "application/json"}
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_S), _client(config) as client:
            async with client.stream(
                method, f"{config.api_url}{path}", headers=headers,
            ) as response:
                # Inclui redirecionamentos: nunca segue Location levando a credencial.
                if response.status_code == 404:
                    raise _not_found()
                if response.status_code != 200:
                    logger.warning("celulares_agente_recusou", status=response.status_code)
                    raise _unavailable()
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                if media_type.lower() != "application/json":
                    raise _invalid_response()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise _invalid_response()
                    body.extend(chunk)
    except (httpx.TimeoutException, TimeoutError):
        logger.warning("celulares_agente_timeout")
        raise CelularesError(
            503, "celulares_tempo_esgotado",
            "O servidor demorou para responder. Atualize o estado antes de repetir uma ação.",
        ) from None
    except (httpx.HTTPError, httpx.InvalidURL, UnicodeError):
        # Não registrar a exceção: ela pode conter URL, headers ou conteúdo privado.
        logger.warning("celulares_agente_indisponivel")
        raise _unavailable() from None
    try:
        return json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise _invalid_response() from None


def _bool_or_none(value: Any) -> bool | None:
    return value if type(value) is bool else None


def _number_or_none(value: Any) -> float | None:
    if type(value) not in {int, float}:
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return round(number, 2) if math.isfinite(number) and number >= 0 else None


def _check_phone(raw: Any, name: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("name") != name:
        raise _invalid_response()
    if raw.get("managed") is not True:
        raise _not_found()
    if type(raw.get("running")) is not bool or type(raw.get("boot_completed")) is not bool:
        raise _invalid_response()
    return raw


def is_camera_pilot(raw: dict[str, Any]) -> bool:
    """Narrow exception for the explicitly enabled, verified offline test clone."""
    isolation = raw.get("isolamento")
    return (
        raw.get("name") == "vrcamera"
        and raw.get("managed") is True and raw.get("read_only") is True
        and raw.get("purpose") == "camera-test" and raw.get("pilot_verified") is True
        and raw.get("egress") == "blocked" and raw.get("proxy") is None
        and raw.get("adb_serial") == "127.0.0.1:5559"
        and isinstance(isolation, dict) and isolation.get("docker_internal") is True
        and not raw.get("last_error")
        and getattr(get_settings(), "celulares_camera_pilot_enabled", False) is True
    )


def phone_out(raw: dict[str, Any]) -> PhoneOut:
    """Seleção positiva de campos: não entrega identity, ADB, caminhos ou proxy."""
    proxy = raw.get("proxy")
    proxy = proxy if isinstance(proxy, dict) and proxy else None
    sidecar = _bool_or_none(raw.get("sidecar_running"))
    tcp = None
    if proxy is not None and sidecar is True:
        if proxy.get("state") == "up":
            tcp = True
        elif proxy.get("state") == "down":
            tcp = False
    isolation = raw.get("isolamento")
    isolation = isolation if isinstance(isolation, dict) else {}
    desired = raw.get("desired")
    if desired not in ("running", "stopped", "removed"):
        desired = None
    egress = {"proxy": "proxy", "blocked": "blocked", "direct-LEAK": "direct"}.get(
        str(raw.get("egress")), "unknown",
    )
    return PhoneOut(
        name=raw["name"], running=raw["running"], boot_completed=raw["boot_completed"],
        read_only=raw.get("read_only") is True or raw["name"] == "vrcamera",
        camera_test=is_camera_pilot(raw),
        camera_available=camera_available(raw),
        desired=desired, sidecar_running=sidecar,
        network=NetworkOut(
            egress=egress, proxy_configured=proxy is not None, proxy_tcp_reachable=tcp,
        ),
        isolation=IsolationOut(**{
            key: _bool_or_none(isolation.get(key))
            for key in ("celguard", "bridge_ipv6_off", "dns_proprio")
        }),
        memory_mb=_number_or_none(raw.get("mem_usage_mb")),
        cpu_percent=_number_or_none(raw.get("cpu_pct")),
        has_error=bool(raw.get("last_error")),
    )


def camera_available(raw: dict[str, Any]) -> bool:
    """Disponibilidade explícita após validação; não muda a exceção do piloto."""
    if is_camera_pilot(raw):
        return True
    configured = getattr(get_settings(), "celulares_camera_enabled_names", "")
    if not isinstance(configured, str) or not configured.strip() or len(configured) > 4096:
        return False
    names = {name.strip() for name in configured.split(",")}
    if len(names) > MAX_NAMES or any(not NAME_RE.fullmatch(name) for name in names):
        return False
    return (
        raw.get("name") in names and raw.get("managed") is True
        and raw.get("running") is True and raw.get("boot_completed") is True
        and not raw.get("last_error")
    )


async def listar() -> PhonesOut:
    config = configuracao()
    if config is None:
        return PhonesOut(enabled=False)
    raw = await _request(config, "GET", "/v1/phones")
    if not isinstance(raw, list):
        raise _invalid_response()
    phones = {}
    for item in raw:
        if not isinstance(item, dict):
            raise _invalid_response()
        name = item.get("name")
        if not isinstance(name, str) or name not in config.allowed_names:
            continue
        # Até um legado colocado por engano na allowlist fica invisível.
        if item.get("managed") is not True:
            continue
        if name in phones:
            raise _invalid_response()
        phones[name] = phone_out(_check_phone(item, name))
    return PhonesOut(enabled=True, phones=[phones[name] for name in sorted(phones)])


async def verificar_aparelho(name: str) -> tuple[AgentConfig, dict[str, Any]]:
    """Retorno interno para ações/sessões: NÃO serializar o registro bruto para a UI."""
    config = _required_config()
    if not NAME_RE.fullmatch(name) or name not in config.allowed_names:
        raise _not_found()
    raw = await _request(config, "GET", f"/v1/phones/{name}")
    return config, _check_phone(raw, name)


async def executar(name: str, action: Action) -> ActionOut:
    if action not in ACTIONS:
        raise _not_found()
    config, phone = await verificar_aparelho(name)
    if name == "vrcamera" or phone.get("read_only") is True:
        raise CelularesError(409, "celular_somente_leitura",
                             "Este aparelho de teste não permite ações de energia pelo painel.")
    raw = await _request(config, "POST", f"/v1/phones/{name}/{action}")
    return ActionOut(action=action, phone=phone_out(_check_phone(raw, name)))
