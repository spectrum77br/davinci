"""O contrato v2 do agente do Mac (o NOSSO conector do Tuta), por cima da Central.

O v1 da Central (`services/mail_central.py`) continua o mesmo, byte a byte: o
v2 só ACRESCENTA, em tabelas nossas (`models/mail_atendimento.py`), o que o
v1 não carrega — e grava o e-mail pelo `mail_central.ingest` DELE (mesmo
cifrado, mesmo dedupe por (caixa, source_id), mesma contagem de anexos).

  sync     — quem é o conector (instância, versões, contadores), as pastas e
             os aliases ATIVOS da conta → quais pastas ler com corpo (o
             servidor decide; o conector obedece) e os aliases que só se
             contam (adm@, financeiro@… sem loja: o corpo nem sobe).
  ingest   — os e-mails, cada um conferido SOZINHO (um ruim não derruba os
             outros 19), só de pasta que o servidor manda ler.
  count    — os ids de uma pasta numa janela: o que falta na Central, o que
             mudou de pasta (aplicado aqui) e o que saiu dela (o conector
             confere: movido para pasta não contada ou apagado de vez).
  changes  — o que o conector viu mudar (pasta nova, apagado).

Nunca grava conteúdo novo de e-mail fora do `content_enc` da Central; em
claro, só ids do Tuta, a chave da pasta e números. Nada aqui commita.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mail import MailMailbox, MailMessage
from app.models.mail_atendimento import (
    MailAgenteV2,
    MailFolder,
    MailMessageMeta,
    MailMessageTuta,
    MailReconciliation,
)
from app.schemas.mail import Ingest
from app.schemas.mail_v2 import ChangesIn, CountIn, IngestV2, MessageInV2, SyncIn
from app.security.cipher import decrypt_json, encrypt_json
from app.services import mail_central
from app.services.mail_atendimento import caixa as config_caixa
from app.services.mail_atendimento import enderecos, pastas, regras, rotear
from app.services.mail_atendimento.constantes import KIND_LIXEIRA, LOCAIS_INTERNOS

CONTRATO = 2
# Outra instância batendo na mesma caixa dentro desta janela = dois agentes.
JANELA_DOIS_AGENTES = timedelta(minutes=3)
# Os contadores do agente valem para o teto da conta por este tempo.
CONTADORES_VALEM_POR = timedelta(minutes=10)
# Teto das listas devolvidas pela contagem.
MAX_DEVOLVIDOS = 500

LER_CORPO = "corpo"
LER_SO_CONTAR = "so_contar"
LER_NAO = "nao"


class MailV2Error(ValueError):
    def __init__(self, code: str, status: int = 409):
        self.code = code
        self.status = status
        super().__init__(code)


def leitura_da_pasta(pasta: MailFolder) -> str:
    """O que o conector faz com a pasta: `corpo` (manda o e-mail), `so_contar`
    (só os ids, o corpo nunca sobe) ou `nao` (uma pessoa mandou ignorar)."""
    if pasta.ler == regras.LER_NAO:
        return LER_NAO
    return LER_SO_CONTAR if pastas.so_contar(pasta) else LER_CORPO


async def _pastas_da_caixa(session: AsyncSession, mailbox_id: UUID) -> dict[str, MailFolder]:
    linhas = (
        await session.execute(select(MailFolder).where(MailFolder.mailbox_id == mailbox_id))
    ).scalars()
    return {p.chave: p for p in linhas}


def _nome(nome: str, caminho: str) -> str:
    return " ".join((nome or regras.nome_da_pasta(caminho) or "").split())[:200] or "?"


async def _sincronizar_pastas(
    session: AsyncSession, mailbox_id: UUID, body: SyncIn, agora: datetime
) -> None:
    """A lista INTEIRA de pastas da conta: nova entra (classificada pela regra),
    renomeada é reclassificada (a escolha de pessoa fica), a que sumiu é marcada."""
    assert body.folders is not None
    r = await regras.carregar(session)
    existentes = await _pastas_da_caixa(session, mailbox_id)
    vistas: set[str] = set()
    for f in body.folders:
        if f.key in vistas:
            continue
        vistas.add(f.key)
        nome = _nome(f.name, f.path)
        caminho = " ".join((f.path or nome).split())[:1000]
        tipo = str(f.kind)
        pasta = existentes.get(f.key)
        if pasta is None:
            await session.execute(
                pg_insert(MailFolder)
                .values(
                    mailbox_id=mailbox_id,
                    chave=f.key,
                    nome=nome,
                    caminho=caminho,
                    tipo_tuta=tipo,
                    pai=f.parent,
                    vista_em=agora,
                )
                .on_conflict_do_nothing(index_elements=["mailbox_id", "chave"])
            )
            pasta = await session.scalar(
                select(MailFolder).where(
                    MailFolder.mailbox_id == mailbox_id, MailFolder.chave == f.key
                )
            )
            assert pasta is not None
            existentes[f.key] = pasta
            regras.reclassificar(pasta, r)
            continue
        if (pasta.nome, pasta.caminho, pasta.tipo_tuta, pasta.pai) != (
            nome,
            caminho,
            tipo,
            f.parent,
        ):
            pasta.nome, pasta.caminho, pasta.tipo_tuta, pasta.pai = nome, caminho, tipo, f.parent
            regras.reclassificar(pasta, r)
        pasta.vista_em = agora
        pasta.sumiu_em = None
    if not body.folders_complete:
        # Lista parcial (uma pasta não decifrou no Mac): ninguém some por isso.
        return
    for chave, pasta in existentes.items():
        if chave not in vistas and pasta.sumiu_em is None:
            pasta.sumiu_em = agora


async def aliases_so_contar(session: AsyncSession, enderecos_da_conta: set[str]) -> list[str]:
    """Os endereços da conta que são INTERNOS (adm@, financeiro@, ti@…) e não são
    de loja nem caixa de site: o e-mail SÓ para eles nem sobe (só é contado)."""
    cad = await rotear.cadastro(session)
    return sorted(
        a
        for a in enderecos_da_conta
        if enderecos.local(a) in LOCAIS_INTERNOS and not cad.e_de_loja(a) and a not in cad.caixas
    )


async def _aliases_que_ficam(
    session: AsyncSession, mailbox: MailMailbox, ativos: set[str]
) -> set[str]:
    """Os aliases ativos da conta que ficam EM CLARO em `mail_agente_v2`.

    Caixa da empresa: todos. Caixa PRIVADA (crítica de 08/10): só os de LOJA e
    os internos "só contar" — os outros endereços pessoais da conta nunca
    ficam em claro no banco (o config da Central os guarda cifrados).
    """
    cfg = await config_caixa.config_da_caixa(session, mailbox.id)
    if cfg.empresa:
        return ativos
    cad = await rotear.cadastro(session)
    so_contar = set(await aliases_so_contar(session, ativos))
    return {a for a in ativos if cad.e_de_loja(a) or a in so_contar}


def _enderecos_da_caixa(mailbox: MailMailbox) -> set[str]:
    config = decrypt_json(mailbox.config_enc)
    todos = [config.get("address"), *(config.get("aliases") or [])]
    return {e for e in (enderecos.normalizar(a) for a in todos) if e}


async def sync(
    session: AsyncSession, mailbox: MailMailbox, body: SyncIn, *, agora: datetime | None = None
) -> dict:
    """O sinal do conector v2 (a linha da caixa já está travada pela rota)."""
    agora = agora or datetime.now(UTC)
    agente = await session.get(MailAgenteV2, mailbox.id)
    if (
        agente is not None
        and agente.instancia != body.instance
        and agente.visto_em is not None
        and agente.visto_em > agora - JANELA_DOIS_AGENTES
    ):
        # Dois Macs (ou dois processos) na mesma conta: o segundo para. O
        # primeiro segue; a Saúde mostra `dois_agentes_em`.
        agente.dois_agentes_em = agora
        raise MailV2Error("another_agent_active", 409)
    if agente is None:
        agente = MailAgenteV2(mailbox_id=mailbox.id, instancia=body.instance, visto_em=agora)
        session.add(agente)
    agente.instancia = body.instance
    agente.versao_agente = body.agent_version or None
    agente.versao_tuta = body.tuta_version or None
    agente.contadores = dict(body.counters)
    agente.visto_em = agora
    ativos = {str(a).strip().lower() for a in body.aliases} if body.aliases is not None else None
    if ativos is not None:
        agente.aliases_conta = sorted(await _aliases_que_ficam(session, mailbox, ativos))
    if body.folders is not None:
        await _sincronizar_pastas(session, mailbox.id, body, agora)
    await session.flush()
    linhas = await _pastas_da_caixa(session, mailbox.id)
    da_conta = (
        _enderecos_da_caixa(mailbox)
        | {str(a) for a in agente.aliases_conta or []}
        | (ativos or set())
    )
    return {
        "contract": CONTRATO,
        "folders": [
            {"key": p.chave, "read": leitura_da_pasta(p)}
            for p in sorted(linhas.values(), key=lambda p: p.chave)
            if p.sumiu_em is None
        ],
        "count_only_aliases": await aliases_so_contar(session, da_conta),
    }


def _campos(erro: ValidationError) -> list[dict]:
    # Como a Central: só o caminho e o tipo do erro, nunca o valor.
    return [
        {"field": ".".join(map(str, item["loc"])), "type": item["type"]}
        for item in erro.errors(include_input=False, include_url=False)[:20]
    ]


async def _garantir_local(
    session: AsyncSession, mailbox_id: UUID, message_id: UUID, folder_key: str
) -> None:
    await session.execute(
        pg_insert(MailMessageTuta)
        .values(message_id=message_id, mailbox_id=mailbox_id, folder_key=folder_key)
        .on_conflict_do_nothing(index_elements=["message_id"])
    )


async def ingest(session: AsyncSession, mailbox: MailMailbox, body: IngestV2) -> dict:
    """Cada e-mail sozinho: inválido, de pasta desconhecida ou de pasta que não se
    lê → `rejected` (com os campos, sem os valores); o resto entra pelo ingest
    da Central (repetido = `duplicate`, nada muda)."""
    linhas = await _pastas_da_caixa(session, mailbox.id)
    resultados: list[dict] = []
    contas = {"accepted": 0, "duplicates": 0, "rejected": 0}
    for bruto in body.messages:
        sid = bruto.get("source_id")
        sid = sid[:191] if isinstance(sid, str) else None
        try:
            item = MessageInV2.model_validate(bruto)
        except ValidationError as erro:
            resultados.append(
                {
                    "source_id": sid,
                    "status": "rejected",
                    "code": "invalid_message",
                    "fields": _campos(erro),
                }
            )
            contas["rejected"] += 1
            continue
        pasta = linhas.get(item.tuta.folder_key)
        if pasta is None or pasta.sumiu_em is not None:
            # Pasta que o /sync ainda não trouxe: o conector sincroniza e manda de novo.
            codigo = "folder_unknown"
        elif leitura_da_pasta(pasta) != LER_CORPO:
            # O corpo de pasta "só contar" nunca é guardado (segunda defesa).
            codigo = "folder_not_read"
        else:
            codigo = None
        if codigo is not None:
            resultados.append({"source_id": sid, "status": "rejected", "code": codigo})
            contas["rejected"] += 1
            continue
        # O MESMO ingest da Central (o subtipo leva os campos do v2 para o cifrado).
        r = await mail_central.ingest(session, mailbox, Ingest.model_construct(messages=[item]))
        message_id = await session.scalar(
            select(MailMessage.id).where(
                MailMessage.mailbox_id == mailbox.id, MailMessage.source_id == item.source_id
            )
        )
        if message_id is not None:
            await _garantir_local(session, mailbox.id, message_id, item.tuta.folder_key)
        status = "accepted" if r["accepted"] else "duplicate"
        contas["accepted" if r["accepted"] else "duplicates"] += 1
        resultados.append({"source_id": sid, "status": status})
    await session.flush()
    return {"results": resultados, **contas}


async def _mover(
    session: AsyncSession,
    message_id: UUID,
    pasta: MailFolder,
    agora: datetime,
    *,
    apagado: bool | None = None,
) -> None:
    """O e-mail agora está em `pasta` no Tuta: a chave, o conteúdo cifrado (a
    tela da Central mostra a pasta certa) e a pasta da meta da ponte."""
    local = await session.get(MailMessageTuta, message_id, with_for_update=True)
    message = await session.get(MailMessage, message_id, with_for_update=True)
    if local is None or message is None:
        return
    lixeira = pasta.tipo_tuta == KIND_LIXEIRA if apagado is None else apagado
    local.folder_key = pasta.chave
    local.movido_em = agora
    local.apagado_em = (local.apagado_em or agora) if lixeira else None
    conteudo = decrypt_json(message.content_enc)
    conteudo["folder"] = (pasta.caminho or pasta.nome or "?")[:128]
    tuta = conteudo.get("tuta") if isinstance(conteudo.get("tuta"), dict) else {}
    tuta.update(
        folder_key=pasta.chave,
        folder_kind=pasta.tipo_tuta,
        folder_path=pasta.caminho or pasta.nome,
        deleted=lixeira,
    )
    conteudo["tuta"] = tuta
    message.content_enc = encrypt_json(conteudo)
    meta = await session.get(MailMessageMeta, message_id)
    # Só a meta que JÁ tem pasta (o e-mail passou no filtro da caixa): a do
    # e-mail privado continua sem nada em claro.
    if meta is not None and meta.folder_id is not None:
        meta.folder_id = pasta.id


async def _apagar(session: AsyncSession, message_id: UUID, agora: datetime) -> None:
    local = await session.get(MailMessageTuta, message_id, with_for_update=True)
    message = await session.get(MailMessage, message_id, with_for_update=True)
    if local is None or message is None or local.apagado_em is not None:
        return
    local.apagado_em = agora
    conteudo = decrypt_json(message.content_enc)
    tuta = conteudo.get("tuta") if isinstance(conteudo.get("tuta"), dict) else {}
    tuta["deleted"] = True
    conteudo["tuta"] = tuta
    message.content_enc = encrypt_json(conteudo)


async def count(
    session: AsyncSession, mailbox: MailMailbox, body: CountIn, *, agora: datetime | None = None
) -> dict:
    """A janela de uma pasta no Tuta × a Central.

    `missing`: no Tuta e não na Central (só pasta de corpo; o conector manda
    de novo). `moved`: estavam como de outra pasta e agora estão nesta
    (aplicado aqui). `left`: a janela é INTEIRA e eles estavam como desta
    pasta e não estão mais (o conector confere cada um: movido para pasta
    que ele não conta, ou apagado de vez).
    """
    agora = agora or datetime.now(UTC)
    pasta = await session.scalar(
        select(MailFolder).where(
            MailFolder.mailbox_id == mailbox.id, MailFolder.chave == body.folder_key
        )
    )
    if pasta is None:
        raise MailV2Error("folder_unknown", 409)
    corpo = leitura_da_pasta(pasta) == LER_CORPO
    ids = list(dict.fromkeys(body.ids))
    presentes: dict[str, tuple[UUID, str | None, datetime | None]] = {}
    if ids:
        for message_id, source_id, chave, apagado_em in (
            await session.execute(
                select(
                    MailMessage.id,
                    MailMessage.source_id,
                    MailMessageTuta.folder_key,
                    MailMessageTuta.apagado_em,
                )
                .outerjoin(MailMessageTuta, MailMessageTuta.message_id == MailMessage.id)
                .where(MailMessage.mailbox_id == mailbox.id, MailMessage.source_id.in_(ids))
            )
        ).all():
            presentes[source_id] = (message_id, chave, apagado_em)
    faltando = [i for i in ids if i not in presentes] if corpo else []
    movidos = 0
    lixeira = pasta.tipo_tuta == KIND_LIXEIRA
    for message_id, chave, apagado_em in presentes.values():
        if chave is None:
            await _garantir_local(session, mailbox.id, message_id, pasta.chave)
            continue
        if chave != pasta.chave or (apagado_em is not None and not lixeira):
            await _mover(session, message_id, pasta, agora)
            movidos += 1
    saiu: list[str] = []
    if body.complete and body.since is not None:
        q = (
            select(MailMessage.source_id)
            .join(MailMessageTuta, MailMessageTuta.message_id == MailMessage.id)
            .where(
                MailMessageTuta.mailbox_id == mailbox.id,
                MailMessageTuta.folder_key == pasta.chave,
                MailMessageTuta.apagado_em.is_(None),
                MailMessage.received_at >= body.since,
            )
            .order_by(MailMessage.received_at)
        )
        if body.until is not None:
            q = q.where(MailMessage.received_at < body.until)
        if ids:
            q = q.where(MailMessage.source_id.not_in(ids))
        saiu = list((await session.execute(q.limit(MAX_DEVOLVIDOS))).scalars())
    if body.complete:
        pasta.ultima_contagem = body.total if body.total is not None else len(ids)
    if body.day is not None:
        await _conciliar(
            session,
            mailbox.id,
            pasta,
            body.day,
            no_tuta=body.total if body.total is not None else len(ids),
            gravados=len(presentes) if corpo else None,
            faltando=len(faltando),
            a_mais=len(saiu),
            completa=body.complete,
            agora=agora,
        )
    await session.flush()
    return {"missing": faltando[:MAX_DEVOLVIDOS], "moved": movidos, "left": saiu}


async def _conciliar(
    session: AsyncSession,
    mailbox_id: UUID,
    pasta: MailFolder,
    dia: date,
    *,
    no_tuta: int,
    gravados: int | None,
    faltando: int,
    a_mais: int,
    completa: bool,
    agora: datetime,
) -> None:
    if not completa:
        motivo = "janela_incompleta"
    elif gravados is None:
        motivo = "so_contar"
    elif faltando or a_mais:
        motivo = "diferenca"
    else:
        motivo = None
    ok = completa and faltando == 0 and a_mais == 0
    valores = {
        "no_tuta": no_tuta,
        "gravados": gravados,
        "faltando": faltando,
        "a_mais": a_mais,
        "ok": ok,
        "motivo": motivo,
        "conferido_em": agora,
    }
    await session.execute(
        pg_insert(MailReconciliation)
        .values(mailbox_id=mailbox_id, folder_id=pasta.id, dia=dia, **valores)
        .on_conflict_do_update(
            index_elements=["mailbox_id", "folder_id", "dia"],
            set_={**valores, "updated_at": func.now()},
        )
    )


async def changes(
    session: AsyncSession, mailbox: MailMailbox, body: ChangesIn, *, agora: datetime | None = None
) -> dict:
    """Pasta nova e apagado de um e-mail já entregue (o repetido no ingest é ignorado)."""
    agora = agora or datetime.now(UTC)
    linhas = await _pastas_da_caixa(session, mailbox.id)
    mudados = desconhecidos = 0
    for mudanca in body.changes:
        message_id = await session.scalar(
            select(MailMessage.id).where(
                MailMessage.mailbox_id == mailbox.id, MailMessage.source_id == mudanca.source_id
            )
        )
        pasta = linhas.get(mudanca.folder_key) if mudanca.folder_key else None
        if message_id is None or (mudanca.folder_key and pasta is None):
            desconhecidos += 1
            continue
        local = await session.get(MailMessageTuta, message_id)
        if local is None:
            conteudo = decrypt_json(
                (await session.get(MailMessage, message_id)).content_enc  # type: ignore[union-attr]
            )
            tuta = conteudo.get("tuta") if isinstance(conteudo.get("tuta"), dict) else {}
            chave_antiga = str(tuta.get("folder_key") or (pasta.chave if pasta else "?"))[:191]
            await _garantir_local(session, mailbox.id, message_id, chave_antiga)
            await session.flush()
            local = await session.get(MailMessageTuta, message_id)
        assert local is not None
        if pasta is not None and (
            local.folder_key != pasta.chave or (mudanca.deleted != (local.apagado_em is not None))
        ):
            await _mover(
                session,
                message_id,
                pasta,
                agora,
                apagado=True if mudanca.deleted else None,
            )
        elif mudanca.deleted:
            await _apagar(session, message_id, agora)
        mudados += 1
    await session.flush()
    return {"updated": mudados, "unknown": desconhecidos}


async def contadores_do_agente(session: AsyncSession, mailbox_id: UUID) -> dict | None:
    """Os contadores que o conector v2 mandou há pouco (None = nada recente)."""
    agente = await session.get(MailAgenteV2, mailbox_id)
    if agente is None or agente.visto_em is None:
        return None
    if agente.visto_em < datetime.now(UTC) - CONTADORES_VALEM_POR:
        return None
    return dict(agente.contadores or {})
