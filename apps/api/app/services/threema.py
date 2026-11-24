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

import re

import httpx
import structlog

from app.config import get_settings
from app.services import threema_registro

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
_CONTEXTOS_ALIASES = {
    "chamados": "logistica",
    "logistica_amazon": "logistica",
    "controle_estoque": "estoque",
    "margem_auto": "margem",
}


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


# Contato avulso (CONTATO EXTERNO): quem recebe aviso mas não tem login no
# DaVinci (ou tem e está desativado). Vinicius, 22/09/2026: precisava mandar
# pro "roma" e não havia onde — a única porta era a lista do `.env`, que exige
# mexer no servidor.
#
# Desde a migration 0507 (tarefa 16, C4) os contatos moram em
# `threema_contato` e são cadastrados só em Cadastros › Envios Threema ›
# Contatos externos, só por admin (`services/threema_envios_edicao`; o
# cadastro pela Ouvidoria saiu na limpeza, C12). A linha antiga
# (`threema_informar_config`, contexto `diretorio`, formato `ID:Nome` do
# `.env`) continua lá como ESPELHO: cada gravação do Cadastro a reescreve com
# os contatos ativos (`espelhar_contatos`), para que reverter o código (que lê
# a linha) não perca ninguém. Sai junto com as fontes antigas (tarefa futura,
# 30 dias depois da F4).
CONTEXTO_CONTATOS = "diretorio"
_THREEMA_ID = re.compile(r"^(?:[A-Z0-9]{8}|\*[A-Z0-9]{7})$")


# Janela da publicação: o código novo pode subir ANTES do `alembic upgrade
# head` (0506/0507; outra pessoa publicando o main também leva este código).
# Enquanto `threema_contato` não existe, a LEITURA dos contatos cai na linha
# antiga (`diretorio`, que a 0507 mantém como espelho) — as telas de sempre
# (Informar, Ouvidoria › Robôs, aviso da IA de Chamado, teste da Denúncia)
# não dão 500. A 1ª leitura boa liga a chave e o processo não confere mais.
_TABELA_CONTATO_OK = False


def _tabela_ausente(exc: BaseException) -> bool:
    """O erro do banco é 'relation does not exist' (SQLSTATE 42P01)? Com
    asyncpg o erro de verdade fica em `exc.orig.__cause__` (como em
    `db.is_unique_violation`)."""
    orig = getattr(exc, "orig", None)
    cause = getattr(orig, "__cause__", None) or orig
    return getattr(cause, "sqlstate", None) == "42P01"


def threema_id_valido(rid: str | None) -> bool:
    """O ID tem o formato do Threema (8 letras/números, ou `*` + 7)."""
    return bool(_THREEMA_ID.match((rid or "").strip().upper()))


async def _linha_contatos(session):
    """A linha de `threema_informar_config` que guardava os contatos (hoje o
    espelho). Import tardio dos modelos, como no `diretorio` (o worker
    importa este módulo antes do registry do SQLAlchemy fechar)."""
    from sqlalchemy import select

    from app.models import ThreemaInformarConfig

    return (
        await session.execute(
            select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == CONTEXTO_CONTATOS)
        )
    ).scalar_one_or_none()


async def _contatos_ativos(session):
    """Os contatos externos ativos (`ThreemaContato`), em ordem alfabética.
    None = a tabela ainda não existe (janela entre subir o código e rodar o
    alembic): quem chama cai na linha antiga. A conferência roda num
    SAVEPOINT, para o erro não envenenar a transação de quem chama."""
    global _TABELA_CONTATO_OK
    from sqlalchemy import func, select
    from sqlalchemy.exc import DBAPIError

    from app.models import ThreemaContato

    consulta = (
        select(ThreemaContato)
        .where(ThreemaContato.ativo.is_(True))
        .order_by(
            func.lower(ThreemaContato.nome),
            ThreemaContato.created_at,
            ThreemaContato.threema_id,
        )
    )
    if _TABELA_CONTATO_OK:
        return (await session.execute(consulta)).scalars().all()
    try:
        async with session.begin_nested():
            linhas = (await session.execute(consulta)).scalars().all()
    except DBAPIError as exc:
        if not _tabela_ausente(exc):
            raise
        logger.warning("threema_contato_tabela_ausente")
        return None
    _TABELA_CONTATO_OK = True
    return linhas


async def contatos(session) -> list[dict[str, str]]:
    """Contatos externos ativos, `[{id, nome}]` (ordem alfabética). Antes do
    alembic (tabela ausente), os da linha antiga, como era antes da 0507."""
    linhas = await _contatos_ativos(session)
    if linhas is None:
        row = await _linha_contatos(session)
        return parse_recipient_directory(row.recipients if row else "", None)
    return [{"id": c.threema_id, "nome": c.nome} for c in linhas]


async def espelhar_contatos(session) -> None:
    """Reescreve a linha antiga (`diretorio`) com os contatos ativos, no
    formato de sempre. Sem contato e sem linha, não cria nada. Quem chama é
    o Cadastro, a cada gravação de contato (o commit fica com ele)."""
    from app.models import ThreemaInformarConfig

    lista = await contatos(session)
    row = await _linha_contatos(session)
    if row is None:
        if not lista:
            return
        row = ThreemaInformarConfig(contexto=CONTEXTO_CONTATOS, recipients="")
        session.add(row)
    row.recipients = ",".join(f"{d['id']}:{d['nome']}" for d in lista)


async def diretorio(session) -> list[dict[str, str]]:
    """Quem pode receber aviso: `[{id, nome}]` pro seletor das telas
    (Informar, Ouvidoria › Robôs).

    Fonte principal: usuários ATIVOS com o campo Threema preenchido em
    Admin › Usuários (sem desativados nem usuários-sistema) — o nome que
    aparece é o do cadastro. Completa com as entradas legadas do `.env`
    (THREEMA_RECIPIENT_NAMES/THREEMA_RECIPIENTS) cujo ID ninguém tem no
    cadastro; quando o dono do código ganhar cadastro, o apelido do `.env`
    dá lugar ao nome real. No meio dos dois entram os CONTATOS EXTERNOS
    ativos (`contatos`, tabela `threema_contato` desde a 0507), pra quem
    recebe aviso e não tem login.
    Cada linha diz de onde veio em `origem` (usuario | contato | env). Só
    leitura: contato externo se cadastra em Cadastros › Envios Threema.
    Ordem alfabética. Import tardio dos modelos: este módulo é importado pelo
    worker antes do registry do SQLAlchemy fechar.
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
    avulsos = await contatos(session)
    env = parse_recipient_directory(s.threema_recipient_names, s.threema_recipients)
    out = [{"id": rid, "nome": nome, "origem": "usuario"} for rid, nome in por_id.items()]
    # Cadastro de usuário manda no nome: quem ganhar login depois aparece com
    # o nome real, e o contato avulso do mesmo ID some sozinho da lista.
    vistos = set(por_id)
    for d in avulsos:
        if d["id"] not in vistos:
            out.append({**d, "origem": "contato"})
            vistos.add(d["id"])
    for d in env:
        if d["id"] not in vistos:
            out.append({**d, "origem": "env"})
            vistos.add(d["id"])
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

        Cada chamada vira um lote no registro (`threema_registro.gravar`), com
        a marca do envio que estiver ativa (`threema_registro.envio`): uma
        linha por destinatário; erro de configuração grava 1 linha e relança;
        erro de rede no meio grava o que já foi tentado e relança. O registro
        é de melhor esforço e não muda nada do envio.
        """
        tentativas: list[threema_registro.Tentativa] = []
        sent: list[str] = []
        failed: list[str] = []
        try:
            self._require_config()
            alvos = recipients or parse_recipients(get_settings().threema_recipients)
            if not alvos:
                raise ThreemaConfigError("threema_recipients_missing")
            for rid in alvos:
                try:
                    mid = await self.send_simple(rid, text)
                    sent.append(rid)
                    tentativas.append(
                        threema_registro.Tentativa(rid, True, 200, str(mid or "") or None)
                    )
                    logger.info(
                        "threema_sent",
                        to=rid,
                        message_id=mid,
                        contexto=self.contexto,
                        canal=self.canal,
                    )
                except ThreemaSendError as e:
                    failed.append(rid)
                    tentativas.append(
                        threema_registro.Tentativa(rid, False, e.status, None, f"http_{e.status}")
                    )
                    logger.warning("threema_recipient_failed", to=rid, status=e.status)
                except Exception as e:
                    # Erro de rede no meio: registra este e relança (como hoje).
                    tentativas.append(threema_registro.Tentativa(rid, False, None, None, _erro(e)))
                    raise
        except Exception as e:
            if not tentativas:
                # Nem chegou a tentar ninguém (config, sem destinatário).
                tentativas.append(threema_registro.Tentativa(None, False, None, None, _erro(e)))
            await self._registrar(text, tentativas)
            raise
        await self._registrar(text, tentativas)
        return {"sent": sent, "failed": failed}

    async def _registrar(self, text: str, tentativas) -> None:
        # getattr: subclasse de teste pode pular o __init__.
        await threema_registro.gravar(
            contexto=getattr(self, "contexto", None),
            canal=getattr(self, "canal", None),
            texto=text,
            tentativas=tentativas,
            segredos=(getattr(self, "secret", "") or "",),
        )


def _erro(e: BaseException) -> str:
    """Código curto do erro pro registro (sem corpo, sem segredo)."""
    if isinstance(e, ThreemaConfigError):
        return str(e)[:200]
    return f"{type(e).__name__}: {str(e)[:150]}"
