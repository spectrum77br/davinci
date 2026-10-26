"""Zap (WhatsApp Cloud API) — credenciais e cadastro: app → WABA → número (RF10, Onda 1).

Desenho: `docs/zap/desenho.md` §3.1, §4.2 e §10.1. A hierarquia mora nas
tabelas `atendimento_zap_(apps|contas|numeros)`; os segredos ficam CIFRADOS
(`security.cipher.encrypt_json`, AES-GCM sobre a `CREDENTIALS_KEY`) e são
decifrados SÓ na memória do processo — nunca no Redis, nunca no log.

  • `cifrar_segredo_app` / `trocar_segredo` — o app secret (que assina o
    webhook). A Meta troca a chave na hora, sem período em que as duas
    valem: na troca DE ROTINA o anterior vale só 1 h; na troca POR
    VAZAMENTO ele é apagado na hora.
  • `segredos_do_app(session, app_id)` — o que o webhook precisa (segredos e
    verify token), com cache de 60 s em memória, inclusive NEGATIVO (o
    `app_id` vai na URL e é público: um curl com ids inventados não consulta
    o banco a cada pedido). `limpar_cache` na troca do segredo.
  • `numero_por_pnid`, `conta_por_waba`, `token_da_conta`.
  • `descobrir_numeros` — só GET na Meta (`zap_api.listar_numeros`).
  • `normalizar_wa_id` — a régua ÚNICA de casar telefone (o 9º dígito
    brasileiro só em número com cara de celular). Só para CASAR, nunca para
    enviar: o `to` do envio usa o `wa_id` como veio.

Nada aqui commita.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoZapApp, AtendimentoZapConta, AtendimentoZapNumero
from app.security.cipher import decrypt_json, encrypt_json

logger = structlog.get_logger()

# Troca DE ROTINA do app secret: o anterior ainda vale por esta janela (a
# Meta já assina com o novo; só cobre o que estava em voo). Por VAZAMENTO: zero.
JANELA_SEGREDO_ANTERIOR = timedelta(hours=1)
MOTIVO_ROTINA = "rotina"
MOTIVO_VAZOU = "vazou"

CACHE_TTL_S = 60.0
# Teto do cache em memória: o negativo guarda ids inventados por estranhos.
CACHE_MAX = 512

# O `app_id` da Meta: só dígitos (vai na URL do webhook).
_APP_ID = re.compile(r"[0-9]{1,32}")


def _agora() -> datetime:
    return datetime.now(UTC)


# ── Telefone ───────────────────────────────────────────────────────────────


def normalizar_wa_id(valor: object) -> str | None:
    """'+55 (11) 9876-5432' → '5511998765432' — SÓ para casar contatos. PURA.

    Só dígitos. No Brasil (55), o celular antigo sem o 9º dígito (DDD + 8
    dígitos começando por 6–9) ganha o 9 — o WhatsApp devolve os dois
    formatos para a mesma pessoa. Fixo (começa por 2–5) fica como está.
    Vazio ou fora do tamanho (8 a 20 dígitos) → None.
    """
    if valor is None:
        return None
    digitos = re.sub(r"\D", "", str(valor))
    if not 8 <= len(digitos) <= 20:
        return None
    if len(digitos) == 12 and digitos.startswith("55") and digitos[4] in "6789":
        digitos = f"{digitos[:4]}9{digitos[4:]}"
    return digitos


# ── Segredo do app ─────────────────────────────────────────────────────────


def gerar_verify_token() -> str:
    """O token do handshake do webhook (gerado pelo DaVinci, aleatório por app)."""
    return secrets.token_urlsafe(32)


def cifrar_segredo_app(
    atual: str, *, anterior: str | None = None, anterior_ate: datetime | None = None
) -> bytes:
    """`{"atual", "anterior", "anterior_ate"}` cifrado para `apps.segredo_enc`."""
    atual = (atual or "").strip()
    if not atual:
        raise ValueError("app secret vazio")
    return encrypt_json(
        {
            "atual": atual,
            "anterior": (anterior or "").strip() or None,
            "anterior_ate": anterior_ate.astimezone(UTC).isoformat() if anterior_ate else None,
        }
    )


def cifrar_verify_token(token: str) -> bytes:
    token = (token or "").strip()
    if not token:
        raise ValueError("verify token vazio")
    return encrypt_json({"verify_token": token})


def trocar_segredo(
    app: AtendimentoZapApp, novo: str, *, motivo: str, agora: datetime | None = None
) -> None:
    """Troca o app secret. Rotina: o anterior vale 1 h. Vazou: o anterior some na hora.

    Limpa o cache do webhook (neste processo; nos outros, o cache vence em
    60 s — o mesmo prazo de qualquer leitura).
    """
    if motivo not in (MOTIVO_ROTINA, MOTIVO_VAZOU):
        raise ValueError("motivo da troca: 'rotina' ou 'vazou'")
    agora = agora or _agora()
    antigo = decrypt_json(app.segredo_enc).get("atual") if motivo == MOTIVO_ROTINA else None
    app.segredo_enc = cifrar_segredo_app(
        novo,
        anterior=antigo,
        anterior_ate=agora + JANELA_SEGREDO_ANTERIOR if antigo else None,
    )
    limpar_cache(app.app_id)


@dataclass(frozen=True)
class SegredosApp:
    """O que o webhook precisa de um app — só na memória (nada de `repr` com segredo)."""

    id: Any
    app_id: str
    teste: bool
    atual: bytes = field(repr=False)
    anterior: bytes | None = field(default=None, repr=False)
    anterior_ate: datetime | None = None
    verify_token: str = field(default="", repr=False)

    def anterior_valido(self, agora: datetime | None = None) -> bytes | None:
        """O segredo anterior, se ainda dentro da janela da troca de rotina."""
        if not self.anterior or self.anterior_ate is None:
            return None
        return self.anterior if (agora or _agora()) < self.anterior_ate else None


_CACHE: dict[str, tuple[float, SegredosApp | None]] = {}


def limpar_cache(app_id: str | None = None) -> None:
    """Esquece o app (ou todos): a próxima leitura vai ao banco."""
    if app_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(str(app_id), None)


def _data(bruto: object) -> datetime | None:
    if not isinstance(bruto, str) or not bruto:
        return None
    try:
        quando = datetime.fromisoformat(bruto)
    except ValueError:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


async def _ler_app(session: AsyncSession, app_id: str) -> SegredosApp | None:
    """Do banco: o app ATIVO com este `app_id`, já decifrado. None = não existe/inativo."""
    app = (
        await session.execute(
            select(AtendimentoZapApp).where(
                AtendimentoZapApp.app_id == app_id, AtendimentoZapApp.ativo.is_(True)
            )
        )
    ).scalar_one_or_none()
    if app is None:
        return None
    segredo = decrypt_json(app.segredo_enc)
    verify = decrypt_json(app.verify_token_enc)
    atual = str(segredo.get("atual") or "")
    if not atual:
        logger.error("zap_app_sem_segredo", app=str(app.id))
        return None
    anterior = segredo.get("anterior")
    return SegredosApp(
        id=app.id,
        app_id=app.app_id,
        teste=bool(app.teste),
        atual=atual.encode(),
        anterior=str(anterior).encode() if anterior else None,
        anterior_ate=_data(segredo.get("anterior_ate")),
        verify_token=str(verify.get("verify_token") or ""),
    )


async def segredos_do_app(session: AsyncSession, app_id: str) -> SegredosApp | None:
    """O app do webhook, com cache de 60 s (positivo E negativo). None = 404.

    `app_id` fora do formato (só dígitos) nem consulta o banco nem entra no
    cache: um id inventado não enche a memória do processo.
    """
    chave = str(app_id or "")
    if not _APP_ID.fullmatch(chave):
        return None
    agora = time.monotonic()
    guardado = _CACHE.get(chave)
    if guardado is not None and guardado[0] > agora:
        return guardado[1]
    app = await _ler_app(session, chave)
    if len(_CACHE) >= CACHE_MAX:
        # Varre os vencidos; se ainda cheio, recomeça (o pior caso é reler).
        for k in [k for k, (vence, _) in _CACHE.items() if vence <= agora]:
            _CACHE.pop(k, None)
        if len(_CACHE) >= CACHE_MAX:
            _CACHE.clear()
    _CACHE[chave] = (agora + CACHE_TTL_S, app)
    return app


# ── WABA e número ─────────────────────────────────────────────────────────


def cifrar_token_conta(
    access_token: str, *, scopes: list[str] | None = None, system_user_id: str | None = None
) -> bytes:
    """O token do usuário do sistema FUNCIONÁRIO, cifrado para `contas.token_enc`."""
    access_token = (access_token or "").strip()
    if not access_token:
        raise ValueError("token vazio")
    return encrypt_json(
        {
            "access_token": access_token,
            "scopes": list(scopes or []),
            "system_user_id": system_user_id,
            "tipo": "system_user",
        }
    )


def token_da_conta(conta: AtendimentoZapConta) -> str:
    """O token decifrado (só na memória; nunca logar, nunca devolver em rota)."""
    token = str(decrypt_json(conta.token_enc).get("access_token") or "")
    if not token:
        raise ValueError("conta sem token")
    return token


async def conta_por_waba(session: AsyncSession, waba_id: str | None) -> AtendimentoZapConta | None:
    if not waba_id:
        return None
    return (
        await session.execute(
            select(AtendimentoZapConta).where(AtendimentoZapConta.waba_id == str(waba_id))
        )
    ).scalar_one_or_none()


async def numero_por_pnid(
    session: AsyncSession, phone_number_id: str | None
) -> AtendimentoZapNumero | None:
    """O número pela chave do webhook (`metadata.phone_number_id`)."""
    if not phone_number_id:
        return None
    return (
        await session.execute(
            select(AtendimentoZapNumero).where(
                AtendimentoZapNumero.phone_number_id == str(phone_number_id)
            )
        )
    ).scalar_one_or_none()


async def numeros_da_conta(
    session: AsyncSession, conta: AtendimentoZapConta
) -> list[AtendimentoZapNumero]:
    return list(
        (
            await session.execute(
                select(AtendimentoZapNumero).where(AtendimentoZapNumero.conta_ref == conta.id)
            )
        )
        .scalars()
        .all()
    )


async def descobrir_numeros(
    conta: AtendimentoZapConta, *, cliente: httpx.AsyncClient | None = None
) -> list[dict]:
    """Os números da WABA na Meta (`GET /{waba}/phone_numbers`) — SÓ leitura.

    Devolve `[{phone_number_id, display_phone_number, verified_name,
    nome_status, quality_rating}]`; quem decide cadastrar é a pessoa (ou o
    script local). Erro da Meta → `zap_api.ZapApiErro` (frase sem token).
    """
    from app.services.atendimento import zap_api

    token = token_da_conta(conta)
    brutos = await zap_api.listar_numeros(conta.waba_id, token, cliente=cliente)
    return [
        {
            "phone_number_id": str(n.get("id") or ""),
            "display_phone_number": str(n.get("display_phone_number") or ""),
            "verified_name": n.get("verified_name"),
            "nome_status": n.get("name_status"),
            "quality_rating": n.get("quality_rating"),
        }
        for n in brutos
        if isinstance(n, dict) and n.get("id")
    ]
