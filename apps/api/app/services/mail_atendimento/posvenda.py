"""Pós-venda › Chamados × e-mail (21/10/2026, decisão 3 do Eduardo).

Eduardo, 21/10/2026: "a ligação pode ser construída sim, só que não vamos linkar
nenhuma pasta a chamado por enquanto, só isso pra não chegar nada lá". A pasta
CRIADA com a categoria "Chamado (Pós-venda)" leva o e-mail ao chamado da
Pós-venda, e não a uma conversa do /atendimento. Nenhuma pasta nasce com essa
categoria (a 0424 não semeia nenhuma): nada chega aqui até uma pessoa escolher.

SÓ ACRESCENTA (regra do dono, 21/10/2026: "não mudar nada do que os Chamados já
fazem"). Este módulo NÃO escreve em nenhuma tabela dos chamados — nem chamado
novo, nem linha no histórico, nem status. O vínculo é só do lado do e-mail
(`mail_message_meta.chamado_id`, que a ponte grava) e a tela do chamado ganha um
bloco novo, só de leitura, com os e-mails ligados (GET /api/chamados/{id}/emails,
`routers/chamados_emails.py`). Por quê:

  • CRIAR chamado tem efeito no resto do sistema: o chamado aberto de um pedido
    entra no contexto do /atendimento (`atendimento/contexto._chamados`), e aí a
    IA deixa de responder sozinha aquele pedido ("pedido com chamado ou
    devolução", `ia.py`; HUMANO_CHAMADO, `humano.py`; EM_ANDAMENTO,
    `agente/checagens.py`), a Logística espelha o chamado na linha do pedido
    (`logistica_ingest.espelhar_chamados`) e as tarefas do Claude o citam
    (`claude_tarefas`). Por isso o e-mail de pedido SEM chamado fica guardado
    (`ignorado`/`chamado_sem_chamado`) para uma pessoa abrir o chamado do jeito
    de sempre e ligar à mão;
  • uma linha no HISTÓRICO do chamado seria lida pela IA de Chamado
    (`/agent/analisar`), entraria no dossiê do Jurídico e na contagem da coluna
    do histórico — mudaria o que já existe.

O contrato com a ponte é `contrato_posvenda.md` (tarefa roteamento, 21/10):

  `ligar_ao_chamado` — o caminho AUTOMÁTICO. A ponte chama UMA vez por e-mail,
      já com a loja, sem suspeito, sem segurança/duplicado/interno, e grava a
      meta a partir do retorno. Aqui, só leitura:
        1. o chamado do PEDIDO citado (o nº único que existe nesta loja), da
           mesma plataforma e conta: pelo nº do marketplace, pelo nº do Bling DA
           LOJA ou pelos pedidos do chamado em lote (`chamado_pedidos`);
        2. sem chamado do pedido, o do CASO rotulado (`chamados.chamado`);
        3. nenhum dos dois: `chamado_id=None` com o motivo — `chamado_sem_chamado`
           (há pedido, não há chamado) ou `chamado_sem_pedido` (nem pedido).
      Mais de um candidato: o NÃO resolvido mais novo; só resolvidos, o mais novo
      deles (o e-mail que chega depois de concluído fica ligado; nada reabre).
  `ligar_a_mao` — a pessoa liga o e-mail guardado a um chamado que ELA escolheu
      (da mesma loja e plataforma). Só escreve a meta do e-mail (`gravado`,
      `chamado_manual`, quem/quando em `meta.alertas`).
  `emails_do_chamado` / `emails_sem_chamado` — o que as telas mostram: o texto
      PROTEGIDO de cada e-mail (como o cartão do /atendimento), decifrado na
      hora, só para quem lê o /atendimento daquela loja (escopo da equipe + a
      política do e-mail por pessoa). Os outros veem só que o e-mail existe.

Nunca commita (a ponte commita POR e-mail); nunca chama nada de fora (HTTP,
robô, fila de envio, Threema); nunca loga texto de e-mail (só ids e estados).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.team_scope import TeamScope
from app.models import (
    BlingOrder,
    Chamado,
    ChamadoPedido,
    MailAttachment,
    MailMessage,
    Store,
    StoreInfo,
    User,
)
from app.models.mail_atendimento import MailFolder, MailMessageMeta
from app.security.cipher import decrypt_json
from app.services import chamados as svc_chamados
from app.services.atendimento import gravar
from app.services.atendimento.escopo import escopo_do_atendimento
from app.services.mail_atendimento import administracao, codigos, ponte, texto
from app.services.mail_atendimento.constantes import (
    ESTADO_GRAVADO,
    ESTADO_IGNORADO,
    IGNORADO_CHAMADO_SEM_PEDIDO,
    VINCULADO_CHAMADO_MANUAL,
)
from app.services.mail_atendimento.suspeito import FRASES as FRASES_SUSPEITO

if TYPE_CHECKING:
    from app.services.mail_atendimento import rotear

logger = structlog.get_logger()

_SP = ZoneInfo("America/Sao_Paulo")

# `meta.vinculado_por` (≤ 24, contrato §2): pelo pedido, pelo caso, pela pessoa.
VINCULADO_PEDIDO = "chamado_pedido"
VINCULADO_CASO = "chamado_caso"
VINCULADO_MANUAL = VINCULADO_CHAMADO_MANUAL
# Os motivos do `ignorado` que a pessoa liga à mão (contrato §4 + regra do dono
# de 21/10: o posvenda não cria chamado). `chamado_sem_pedido`: o e-mail não
# identifica pedido desta loja nem caso. `chamado_sem_chamado`: identifica o
# pedido, mas o pedido não tem chamado (abrir é com a pessoa, na aba Chamados).
MOTIVO_SEM_PEDIDO = IGNORADO_CHAMADO_SEM_PEDIDO
MOTIVO_SEM_CHAMADO = "chamado_sem_chamado"
MOTIVOS_PARA_LIGAR = (MOTIVO_SEM_PEDIDO, MOTIVO_SEM_CHAMADO)
# As telas listam no máximo isto.
LIMITE_EMAILS = 100


@dataclass(frozen=True)
class LigacaoChamado:
    """O retorno para a ponte (contrato §2). `chamado_id` None = não ligou; aí
    `motivo` diz por quê (o `meta.motivo` do `ignorado`)."""

    chamado_id: UUID | None
    criado: bool = False  # sempre False: o posvenda não cria chamado (regra do dono)
    vinculado_por: str | None = None
    pedido: str | None = None
    chamado_mensagem_id: UUID | None = None  # sempre None: nada vai para o histórico
    motivo: str | None = None


class LigacaoError(Exception):
    """Falha de negócio do "ligar à mão", com código legível para a rota (409)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


# ── A loja do e-mail ──────────────────────────────────────────────────────


def _nome(valor: str | None) -> str:
    return " ".join((valor or "").split()).lower()


@dataclass
class _Loja:
    """A loja do e-mail, como os chamados a gravam (`plataforma` livre e `conta`)."""

    plataforma: str  # canônica (`chamados.plataforma_canonica`)
    integration_id: UUID | None
    nomes: set[str] = field(default_factory=set)  # contas aceitas (minúsculas)
    lojas_do_bling: set[str] = field(default_factory=set)


async def _loja(
    session: AsyncSession,
    *,
    plataforma: str | None,
    integration_id: UUID | None,
    store_info_id: UUID | None,
    nome_da_rota: str | None = None,
) -> _Loja:
    fichas: list[StoreInfo] = []
    if store_info_id is not None and (ficha := await session.get(StoreInfo, store_info_id)):
        fichas.append(ficha)
    if integration_id is not None:
        fichas += [
            f
            for f in (
                await session.scalars(
                    select(StoreInfo).where(StoreInfo.integration_id == integration_id)
                )
            ).all()
            if f.id != store_info_id
        ]
    canonica = svc_chamados.plataforma_canonica(plataforma)
    # Só as fichas da MESMA plataforma: a ficha mal cadastrada não empresta o
    # nome a outra loja.
    fichas = [
        f
        for f in fichas
        if not (f.platform or "").strip()
        or svc_chamados.plataforma_canonica(f.platform) == canonica
    ]
    nomes = {_nome(f.account_name) for f in fichas if (f.account_name or "").strip()}
    if (nome_da_rota or "").strip():
        nomes.add(_nome(nome_da_rota))
    return _Loja(
        plataforma=canonica,
        integration_id=integration_id,
        nomes=nomes,
        lojas_do_bling={
            (f.bling_store_id or "").strip() for f in fichas if (f.bling_store_id or "").strip()
        },
    )


def _da_loja(ch: Chamado, loja: _Loja) -> bool:
    """O chamado é desta loja? Plataforma igual; a conta, quando as duas têm nome."""
    plataforma = svc_chamados.plataforma_canonica(ch.plataforma)
    if plataforma and loja.plataforma and plataforma != loja.plataforma:
        return False
    conta = _nome(ch.conta)
    return not (conta and loja.nomes and conta not in loja.nomes)


async def _pedido_bling(session: AsyncSession, loja: _Loja, numero: str) -> str | None:
    """O nº do Bling do pedido DESTA loja (a mesma regra de `pedido.existe_na_loja`):
    pela integração da empresa da linha do espelho, ou pela loja do Bling da ficha.
    A linha de OUTRA loja nunca empresta o número."""
    linhas = (
        await session.execute(
            select(BlingOrder.numero, BlingOrder.store_id, Store.integration_id, BlingOrder.loja)
            .outerjoin(Store, Store.id == BlingOrder.store_id)
            .where(BlingOrder.numeroloja == numero, BlingOrder.numero.is_not(None))
            .order_by(BlingOrder.data.desc().nulls_last(), BlingOrder.numero)
            .limit(20)
        )
    ).all()
    for numero_bling, store_id, integracao, loja_bling in linhas:
        if loja.integration_id is not None:
            if store_id is None or integracao == loja.integration_id:
                return numero_bling
        elif (loja_bling or "").strip() in loja.lojas_do_bling:
            return numero_bling
    return None


def _escolher(candidatos: list[Chamado], loja: _Loja) -> Chamado | None:
    """O NÃO resolvido mais novo; só resolvidos, o mais novo deles. Determinístico."""
    da_loja = [c for c in candidatos if _da_loja(c, loja)]
    if not da_loja:
        return None
    da_loja.sort(key=lambda c: (c.resolvido, -c.created_at.timestamp(), str(c.id)))
    return da_loja[0]


async def _do_pedido(
    session: AsyncSession, loja: _Loja, numero: str, pedido_bling: str | None
) -> Chamado | None:
    alvo = numero.strip().upper()
    lote = [func.upper(func.trim(ChamadoPedido.pedido_marketplace)) == alvo]
    conds = [func.upper(func.trim(Chamado.pedido_marketplace)) == alvo]
    if pedido_bling:
        conds.append(Chamado.pedido_bling == pedido_bling)
        lote.append(ChamadoPedido.pedido_bling == pedido_bling)
    conds.append(Chamado.id.in_(select(ChamadoPedido.chamado_id).where(or_(*lote))))
    candidatos = list((await session.scalars(select(Chamado).where(or_(*conds)))).all())
    return _escolher(candidatos, loja)


async def _do_caso(session: AsyncSession, loja: _Loja, caso_id: str) -> Chamado | None:
    candidatos = list(
        (
            await session.scalars(
                select(Chamado).where(func.trim(Chamado.chamado) == caso_id.strip())
            )
        ).all()
    )
    return _escolher(candidatos, loja)


# ── O caminho automático (a ponte) ────────────────────────────────────────


async def ligar_ao_chamado(
    session: AsyncSession,
    email: ponte.Email,
    pasta: MailFolder,
    *,
    meta: MailMessageMeta,
    rota: rotear.Rota,
    protegido: codigos.Protegido,
    pedido: str | None,
    pedidos_citados: list[str],
    caso_id: str | None,
    agora: datetime,
) -> LigacaoChamado:
    """O e-mail da pasta "Chamado (Pós-venda)" → o chamado que JÁ existe (contrato §3).

    Só leitura: não escreve em chamados nem na `meta` (a ponte grava a partir do
    retorno). `email`, `pasta`, `protegido`, `pedidos_citados` e `agora` fazem
    parte do contrato, mas aqui não entram: nada do e-mail vai para o chamado, e
    só o nº que EXISTE nesta loja (`pedido`) liga."""
    if meta.chamado_id is not None:
        # A ponte chama uma vez só; se chamar de novo, a mesma resposta.
        return LigacaoChamado(
            chamado_id=meta.chamado_id,
            vinculado_por=meta.vinculado_por,
            pedido=meta.pedido_marketplace,
        )
    pedido = (pedido or "").strip() or None
    caso_id = (caso_id or "").strip() or None
    if pedido is None and caso_id is None:
        return LigacaoChamado(chamado_id=None, motivo=MOTIVO_SEM_PEDIDO)
    loja = await _loja(
        session,
        plataforma=rota.plataforma or meta.plataforma,
        integration_id=rota.integration_id or meta.integration_id,
        store_info_id=rota.store_info_id or meta.store_info_id,
        nome_da_rota=rota.loja_nome,
    )
    ch: Chamado | None = None
    vinculado = VINCULADO_PEDIDO
    if pedido is not None:
        ch = await _do_pedido(session, loja, pedido, await _pedido_bling(session, loja, pedido))
    if ch is None and caso_id is not None:
        ch = await _do_caso(session, loja, caso_id)
        vinculado = VINCULADO_CASO
    if ch is None:
        motivo = MOTIVO_SEM_CHAMADO if pedido is not None else MOTIVO_SEM_PEDIDO
        logger.info("posvenda_email_sem_chamado", message_id=str(meta.message_id), motivo=motivo)
        return LigacaoChamado(chamado_id=None, pedido=pedido, motivo=motivo)
    logger.info(
        "posvenda_email_ligado",
        message_id=str(meta.message_id),
        chamado_id=str(ch.id),
        vinculado_por=vinculado,
    )
    return LigacaoChamado(
        chamado_id=ch.id,
        vinculado_por=vinculado,
        pedido=ch.pedido_marketplace or pedido,
    )


# ── Ligar à mão (o e-mail guardado) ───────────────────────────────────────


async def ligar_a_mao(
    session: AsyncSession,
    meta: MailMessageMeta,
    chamado: Chamado,
    user: User,
    *,
    agora: datetime | None = None,
) -> LigacaoChamado:
    """Uma PESSOA liga o e-mail guardado (`chamado_sem_pedido`/`chamado_sem_chamado`)
    ao chamado que ela escolheu, da mesma loja e plataforma (contrato §6). Escreve
    só a meta do e-mail: `gravado`, `chamado_manual`, e quem/quando em
    `meta.alertas` (o formato de `_registrar_pessoa`). Não toca no chamado. Não
    commita. Quem chama já conferiu que a pessoa lê este e-mail e mexe no
    /atendimento."""
    agora = agora or datetime.now(UTC)
    if meta.estado != ESTADO_IGNORADO or meta.motivo not in MOTIVOS_PARA_LIGAR:
        raise LigacaoError("email_nao_esta_para_ligar")
    if meta.chamado_id is not None:
        raise LigacaoError("email_ja_ligado")
    if meta.integration_id is None and meta.store_info_id is None:
        raise LigacaoError("email_sem_loja")
    loja = await _loja(
        session,
        plataforma=meta.plataforma,
        integration_id=meta.integration_id,
        store_info_id=meta.store_info_id,
    )
    if not _da_loja(chamado, loja):
        raise LigacaoError("chamado_de_outra_loja")
    quem = user.name or user.email
    meta.estado = ESTADO_GRAVADO
    meta.motivo = None
    meta.chamado_id = chamado.id
    meta.vinculado_por = VINCULADO_MANUAL
    if chamado.pedido_marketplace:
        meta.pedido_marketplace = chamado.pedido_marketplace[:64]
    meta.processado_em = agora
    quando = agora.astimezone(_SP).strftime("%d/%m/%Y %H:%M")
    rotulo = chamado.chamado or chamado.pedido_bling or chamado.pedido_marketplace or "sem nº"
    meta.alertas = [
        *(meta.alertas or []),
        {
            "codigo": "ligado_ao_chamado_por_pessoa",
            "texto": f"ligado ao chamado {rotulo} por {quem} em {quando}"[:300],
            "por": str(user.id),
            "em": agora.isoformat(),
        },
    ]
    logger.info(
        "posvenda_email_ligado_a_mao",
        message_id=str(meta.message_id),
        chamado_id=str(chamado.id),
        user_id=str(user.id),
    )
    return LigacaoChamado(
        chamado_id=chamado.id,
        vinculado_por=VINCULADO_MANUAL,
        pedido=chamado.pedido_marketplace,
    )


# ── O que as telas mostram ────────────────────────────────────────────────


def _no_escopo(scope: TeamScope, meta: MailMessageMeta) -> bool:
    if scope.unrestricted:
        return True
    if meta.integration_id is not None:
        return meta.integration_id in scope.integration_ids
    return meta.store_info_id is not None and meta.store_info_id in scope.store_info_ids


def _no_escopo_sql(scope: TeamScope):
    """O mesmo `_no_escopo`, no SQL (a lista filtra ANTES do limite)."""
    if scope.unrestricted:
        return None
    m = MailMessageMeta
    return or_(
        m.integration_id.in_(list(scope.integration_ids)) if scope.integration_ids else false(),
        and_(
            m.integration_id.is_(None),
            m.store_info_id.in_(list(scope.store_info_ids)) if scope.store_info_ids else false(),
        ),
    )


async def visiveis(
    session: AsyncSession, user: User, metas: list[MailMessageMeta], *, ve_o_atendimento: bool
) -> set[UUID]:
    """Os e-mails que a pessoa LÊ: a trava do /atendimento (`ve_o_atendimento`, a
    rota decide), a loja no escopo da equipe e a política do e-mail por pessoa
    (`administracao.filtro_mensagem`, com a lixeira) — ANTES de decifrar."""
    if not ve_o_atendimento or not metas:
        return set()
    scope = await escopo_do_atendimento(session, user)
    ids = [m.message_id for m in metas if _no_escopo(scope, m)]
    if not ids:
        return set()
    return set(
        await session.scalars(
            select(MailMessage.id).where(
                MailMessage.id.in_(ids), administracao.filtro_mensagem(user)
            )
        )
    )


async def _nomes_das_fichas(session: AsyncSession, ids: set[UUID]) -> dict[UUID, str]:
    if not ids:
        return {}
    return {
        f.id: " ".join((f.account_name or "").split()) or (f.platform or "")
        for f in (await session.scalars(select(StoreInfo).where(StoreInfo.id.in_(ids)))).all()
    }


async def _cartoes(
    session: AsyncSession,
    user: User,
    linhas: list[tuple[MailMessageMeta, MailMessage]],
    *,
    ve_o_atendimento: bool,
) -> list[dict[str, Any]]:
    """Um cartão por e-mail. Quem LÊ (ver `visiveis`): o texto protegido — links
    de acesso fora, código mascarado, nunca HTML —, o remetente e os anexos (nome
    e tamanho). Quem não lê: só que o e-mail existe (data, pasta, loja)."""
    if not linhas:
        return []
    metas = [meta for meta, _message in linhas]
    pode_ler = await visiveis(session, user, metas, ve_o_atendimento=ve_o_atendimento)
    pastas = {
        p.id: p.nome
        for p in (
            await session.scalars(
                select(MailFolder).where(
                    MailFolder.id.in_({m.folder_id for m in metas if m.folder_id})
                )
            )
        ).all()
    }
    lojas = await _nomes_das_fichas(session, {m.store_info_id for m in metas if m.store_info_id})
    anexos: dict[UUID, list[dict[str, Any]]] = {}
    lidos = [m.message_id for m in metas if m.message_id in pode_ler]
    if lidos:
        for a in (
            await session.scalars(
                select(MailAttachment)
                .where(MailAttachment.message_id.in_(lidos))
                .order_by(MailAttachment.id)
            )
        ).all():
            info = decrypt_json(a.metadata_enc)
            anexos.setdefault(a.message_id, []).append(
                {
                    "id": str(a.id),
                    "tamanho": a.size,
                    **{k: v for k, v in info.items() if k in ("filename", "content_type")},
                }
            )
    itens: list[dict[str, Any]] = []
    for meta, message in linhas:
        item: dict[str, Any] = {
            "id": str(meta.message_id),
            "chamado_id": str(meta.chamado_id) if meta.chamado_id else None,
            "recebido_em": gravar._utc(message.received_at),
            "pasta": pastas.get(meta.folder_id) if meta.folder_id else None,
            "loja": lojas.get(meta.store_info_id) if meta.store_info_id else None,
            "plataforma": meta.plataforma,
            "pedido": meta.pedido_marketplace,
            "motivo": meta.motivo,
            "vinculado_por": meta.vinculado_por,
            "pode_ler": meta.message_id in pode_ler,
            # O remetente que imita a plataforma (revisão de 21/10): quem liga à
            # mão precisa ver ANTES — o cartão do /atendimento mostra o mesmo.
            "suspeito": bool(meta.suspeito),
        }
        if item["pode_ler"]:
            email = ponte.decifrar(message)
            protegido = codigos.proteger(email.assunto, texto.legivel(email.texto))
            item.update(
                {
                    "de": email.de,
                    "de_nome": email.de_nome,
                    "assunto": protegido.assunto,
                    "texto": protegido.texto,
                    "links_removidos": protegido.links_removidos,
                    "codigo_mascarado": meta.codigo_mascarado or protegido.codigo_mascarado,
                    "anexos": anexos.get(meta.message_id, []),
                    "suspeito_motivos": [
                        FRASES_SUSPEITO.get(m, m) for m in (meta.suspeito_motivos or [])
                    ],
                }
            )
        itens.append(item)
    return itens


async def emails_do_chamado(
    session: AsyncSession, user: User, chamado_ids: list[UUID], *, ve_o_atendimento: bool
) -> dict[str, Any]:
    """Os e-mails ligados ao chamado (às linhas do caso), do mais antigo ao mais novo."""
    linhas = (
        await session.execute(
            select(MailMessageMeta, MailMessage)
            .join(MailMessage, MailMessage.id == MailMessageMeta.message_id)
            .where(
                MailMessageMeta.chamado_id.in_(chamado_ids),
                MailMessageMeta.estado == ESTADO_GRAVADO,
            )
            .order_by(MailMessage.received_at, MailMessageMeta.message_id)
            .limit(LIMITE_EMAILS)
        )
    ).all()
    itens = await _cartoes(
        session, user, [(m, msg) for m, msg in linhas], ve_o_atendimento=ve_o_atendimento
    )
    return {
        "total": len(itens),
        "ocultos": sum(1 for i in itens if not i["pode_ler"]),
        "itens": itens,
    }


async def emails_sem_chamado(
    session: AsyncSession, user: User, *, ve_o_atendimento: bool
) -> dict[str, Any]:
    """Os e-mails guardados para ligar à mão (`chamado_sem_pedido`/`chamado_sem_chamado`)
    que a pessoa LÊ, do mais novo ao mais antigo (até `LIMITE_EMAILS`). Os
    outros entram só em `ocultos`.

    O filtro de quem lê (a loja no escopo da equipe e a política do e-mail por
    pessoa — o mesmo de `visiveis`) roda NO SQL, antes do limite (revisão de
    21/10): os 100 mais novos do banco inteiro podiam ser todos de lojas que a
    pessoa não vê, e a lista dela saía vazia com e-mails dela guardados."""
    guardado = (
        MailMessageMeta.estado == ESTADO_IGNORADO,
        MailMessageMeta.motivo.in_(MOTIVOS_PARA_LIGAR),
        MailMessageMeta.chamado_id.is_(None),
    )
    todos = int(
        await session.scalar(select(func.count()).select_from(MailMessageMeta).where(*guardado))
        or 0
    )
    if not ve_o_atendimento or not todos:
        return {"total": 0, "ocultos": todos, "itens": []}
    scope = await escopo_do_atendimento(session, user)
    filtros = [*guardado, administracao.filtro_mensagem(user)]
    if (escopo := _no_escopo_sql(scope)) is not None:
        filtros.append(escopo)
    lidos_total = int(
        await session.scalar(
            select(func.count())
            .select_from(MailMessageMeta)
            .join(MailMessage, MailMessage.id == MailMessageMeta.message_id)
            .where(*filtros)
        )
        or 0
    )
    linhas = (
        await session.execute(
            select(MailMessageMeta, MailMessage)
            .join(MailMessage, MailMessage.id == MailMessageMeta.message_id)
            .where(*filtros)
            .order_by(MailMessage.received_at.desc(), MailMessageMeta.message_id)
            .limit(LIMITE_EMAILS)
        )
    ).all()
    itens = await _cartoes(
        session, user, [(m, msg) for m, msg in linhas], ve_o_atendimento=ve_o_atendimento
    )
    # `_cartoes` confere de novo, linha a linha (a mesma regra): só o que ela lê.
    lidos = [i for i in itens if i["pode_ler"]]
    return {"total": lidos_total, "ocultos": todos - lidos_total, "itens": lidos}


async def anexo_do_chamado(
    session: AsyncSession,
    user: User,
    attachment: MailAttachment,
    *,
    ve_o_atendimento: bool,
) -> bool:
    """O anexo é de um e-mail ligado a um chamado E a pessoa lê esse e-mail?"""
    meta = await session.get(MailMessageMeta, attachment.message_id)
    if meta is None or meta.chamado_id is None or meta.estado != ESTADO_GRAVADO:
        return False
    return meta.message_id in await visiveis(
        session, user, [meta], ve_o_atendimento=ve_o_atendimento
    )
