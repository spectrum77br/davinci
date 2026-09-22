"""Threema Gateway (Basic mode) — notificações privadas por assunto.

Basic mode: o servidor do Threema criptografa a mensagem pro destinatário; a
gente só faz um POST com `from`/`to`/`text`/`secret`. Uma chamada por
destinatário (Basic não faz broadcast). Config vem do `.env`
(`threema_gateway_id`/`threema_gateway_secret`/`threema_recipients`); sem isso
`send_to_all` levanta ThreemaConfigError.

`contexto` seleciona o ID Gateway do assunto, mantendo os destinatários.
IDs distintos geram conversas privadas distintas no Threema. O par global
é usado por contextos explicitamente destinados ao canal Geral no mapa
`threema_context_channels` e por canais ainda não configurados na transição
(`threema_separate_chats=False`). Nenhuma mensagem muda de assunto pelo texto.
O destino `desativado` bloqueia os envios do contexto, inclusive na transição.

Doc: https://gateway.threema.ch/en/developer/api — `POST /send_simple`
(form-urlencoded). Sucesso = 200 com o message id no corpo. Erros mapeados:
401 auth, 402 sem crédito, 404 destinatário inexistente, 413 texto grande.
"""

from __future__ import annotations

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()

THREEMA_API_BASE = "https://msgapi.threema.ch"
# Basic mode aceita até 3500 bytes de texto por mensagem.
_MAX_TEXT_BYTES = 3500

_CONTEXTOS = frozenset(
    {
        "logistica",
        "margem",
        "estoque",
        "devolucoes",
        "juridico",
        "importacao",
        "flex",
    }
)
_CONTEXTOS_ALIASES = {"controle_estoque": "estoque", "margem_auto": "margem"}


class ThreemaConfigError(RuntimeError):
    """Gateway ID / secret / destinatários ausentes."""


class ThreemaSendError(RuntimeError):
    """Falha ao enviar uma mensagem (status != 200)."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"threema_send_{status}: {body[:120]}")


def parse_recipients(raw: str | None) -> list[str]:
    """IDs separados por vírgula/espaço/; → lista limpa (8 chars, upper)."""
    if not raw:
        return []
    out: list[str] = []
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        rid = part.strip().upper()
        if rid:
            out.append(rid)
    return out


def parse_recipient_directory(names_raw: str | None, ids_raw: str | None) -> list[dict[str, str]]:
    """Diretório `[{id, nome}]` dos destinatários pro seletor do front.

    Nomes vêm de `names_raw` (`ID:Nome` separados por vírgula/;); IDs sem nome
    caem no próprio ID. Completa com os IDs de `ids_raw` que ficaram sem nome
    (nome = ID). Preserva a ordem: primeiro os nomeados, depois os avulsos.
    """
    names: dict[str, str] = {}
    order: list[str] = []
    for part in (names_raw or "").replace(";", ",").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        rid, _, nome = part.partition(":")
        rid = rid.strip().upper()
        nome = nome.strip()
        if rid and rid not in names:
            names[rid] = nome or rid
            order.append(rid)
    for rid in parse_recipients(ids_raw):
        if rid not in names:
            names[rid] = rid
            order.append(rid)
    return [{"id": rid, "nome": names[rid]} for rid in order]


async def diretorio(session) -> list[dict[str, str]]:
    """Quem pode receber aviso: `[{id, nome}]` pro seletor das telas
    (Informar, Ouvidoria › Robôs).

    Fonte principal: usuários ATIVOS com o campo Threema preenchido em
    Admin › Usuários (sem desativados nem usuários-sistema) — o nome que
    aparece é o do cadastro. Completa com as entradas legadas do `.env`
    (THREEMA_RECIPIENT_NAMES/THREEMA_RECIPIENTS) cujo ID ninguém tem no
    cadastro; quando o dono do código ganhar cadastro, o apelido do `.env`
    dá lugar ao nome real. Ordem alfabética. Import tardio dos modelos: este
    módulo é importado pelo worker antes do registry do SQLAlchemy fechar.
    """
    from sqlalchemy import func, select

    from app.models import User, UserStatus

    rows = (
        (
            await session.execute(
                select(User).where(
                    User.threema.is_not(None),
                    func.trim(User.threema) != "",
                    User.status == UserStatus.ACTIVE,
                    User.disabled_at.is_(None),
                    User.open_id.notlike("system:%"),
                )
            )
        )
        .scalars()
        .all()
    )
    por_id: dict[str, str] = {}
    for u in rows:
        # parse_recipients normaliza (maiúsculas, separadores) — aceita o
        # campo como for digitado.
        for rid in parse_recipients(u.threema):
            por_id.setdefault(rid, u.name or u.email)
    s = get_settings()
    env = parse_recipient_directory(s.threema_recipient_names, s.threema_recipients)
    out = [{"id": rid, "nome": nome} for rid, nome in por_id.items()]
    out += [d for d in env if d["id"] not in por_id]
    out.sort(key=lambda d: (d["nome"] or "").lower())
    return out


def compose_texto(texto: str, *, pedido: str | None = None, loja: str | None = None) -> str:
    """Prefixa `Pedido X | Loja Y` no topo da mensagem quando houver (pra o
    destinatário saber a qual pedido/loja o aviso se refere)."""
    cabecalho = []
    if (pedido or "").strip():
        cabecalho.append(f"Pedido {pedido.strip()}")
    if (loja or "").strip():
        cabecalho.append(f"Loja {loja.strip()}")
    if cabecalho:
        return " | ".join(cabecalho) + "\n" + texto
    return texto


class ThreemaClient:
    def __init__(
        self,
        gateway_id: str | None = None,
        secret: str | None = None,
        *,
        contexto: str | None = None,
    ) -> None:
        s = get_settings()
        contexto = (contexto or "").strip().lower()
        self.contexto = _CONTEXTOS_ALIASES.get(contexto, contexto)
        self.canal = self.contexto or "geral"
        self._config_error: str | None = None
        context_channels: dict[str, str] = {}
        for source, target in s.threema_context_channels.items():
            source, target = source.strip().lower(), target.strip().lower()
            source = _CONTEXTOS_ALIASES.get(source, source)
            if (
                source not in _CONTEXTOS
                or target not in _CONTEXTOS | {"geral", "desativado"}
                or (source in context_channels and context_channels[source] != target)
            ):
                self._config_error = "threema_context_channels_invalid"
            else:
                context_channels[source] = target
        separado = s.threema_separate_chats
        selected_id = s.threema_gateway_id
        selected_secret = s.threema_gateway_secret
        if self.contexto:
            if self.contexto not in _CONTEXTOS:
                self._config_error = "threema_contexto_desconhecido"
            else:
                self.canal = context_channels.get(self.contexto, self.contexto)
                if self.disabled:
                    self._config_error = "threema_contexto_desativado"
                    selected_id, selected_secret = "", ""
                elif self.canal != "geral":
                    channel_id = getattr(s, f"threema_{self.canal}_gateway_id").strip()
                    channel_secret = getattr(s, f"threema_{self.canal}_gateway_secret").strip()
                    # Um par incompleto nunca é combinado com credenciais globais.
                    if (
                        channel_id
                        or channel_secret
                        or separado
                        or self.contexto in context_channels
                    ):
                        selected_id, selected_secret = channel_id, channel_secret
                    else:
                        self.canal = "geral"
        elif separado:
            self._config_error = "threema_contexto_missing"
        self.gateway_id = (gateway_id or selected_id or "").strip()
        self.secret = (secret or selected_secret or "").strip()
        if separado and not self._config_error and self.gateway_id:
            # Contextos agrupados são um único canal efetivo. A resolução é
            # direta: valores do mapa identificam credenciais, não outro contexto.
            effective_channels = {
                context_channels.get(context, context) for context in _CONTEXTOS
            } - {"desativado"}
            channel_ids = {
                channel: (
                    s.threema_gateway_id
                    if channel == "geral"
                    else getattr(s, f"threema_{channel}_gateway_id")
                ).strip()
                for channel in effective_channels
            }
            for other, other_id in channel_ids.items():
                if other != self.canal and other_id and other_id.upper() == self.gateway_id.upper():
                    self._config_error = "threema_contexto_gateway_id_repetido"
                    break

    @property
    def disabled(self) -> bool:
        return self.canal == "desativado"

    def _require_config(self) -> None:
        # Valida no envio, dentro dos try/except já usados pelos notificadores.
        if self._config_error:
            raise ThreemaConfigError(self._config_error)
        if not self.gateway_id:
            raise ThreemaConfigError("threema_gateway_id_missing")
        if not self.secret:
            raise ThreemaConfigError("threema_gateway_secret_missing")

    async def send_simple(self, to: str, text: str) -> str:
        """Envia pra 1 destinatário. Retorna o message id do Threema."""
        self._require_config()
        body = text.encode("utf-8")[:_MAX_TEXT_BYTES].decode("utf-8", "ignore")
        payload = {
            "from": self.gateway_id,
            "to": to.strip().upper(),
            "text": body,
            "secret": self.secret,
        }
        async with httpx.AsyncClient(timeout=15) as cli:
            resp = await cli.post(f"{THREEMA_API_BASE}/send_simple", data=payload)
        if resp.status_code != 200:
            logger.warning("threema_send_failed", status=resp.status_code, body=resp.text[:200])
            raise ThreemaSendError(resp.status_code, resp.text)
        return resp.text.strip()

    async def send_to_all(
        self, text: str, recipients: list[str] | None = None
    ) -> dict[str, list[str]]:
        """Envia o mesmo texto pra cada destinatário configurado.

        Retorna `{"sent": [ids ok], "failed": [ids que falharam]}`. Não
        interrompe no 1º erro — tenta todos.
        """
        self._require_config()
        alvos = recipients or parse_recipients(get_settings().threema_recipients)
        if not alvos:
            raise ThreemaConfigError("threema_recipients_missing")
        sent: list[str] = []
        failed: list[str] = []
        for rid in alvos:
            try:
                mid = await self.send_simple(rid, text)
                sent.append(rid)
                logger.info(
                    "threema_sent",
                    to=rid,
                    message_id=mid,
                    contexto=self.contexto,
                    canal=self.canal,
                )
            except ThreemaSendError as e:
                failed.append(rid)
                logger.warning("threema_recipient_failed", to=rid, status=e.status)
        return {"sent": sent, "failed": failed}
