"""Garantia automática (28/10/2026) — o robô cria a garantia e liga as conversas.

Pedido do Eduardo: a garantia do pedido Uranyx "cai sozinha" no Painel de
Garantia, ligada pelo número da NOTA FISCAL, com o histórico das conversas
(fotos e vídeos) no estilo do Atendimento — sem o "Vincular à garantia" →
"Nova garantia" → "Cadastrar" de hoje.

O robô faz o MESMO que a pessoa faria na janela "Nova garantia", com a nota
que ela mesma sugere (`PedidoInfo.nota_produto`), e é mais exigente:

1. Pedido do Bling com item da linha Uranyx (`ferramentas.linha_do_item`: o
   `a0NN` só com "uranyx" no nome — a balança de mala da Charlots fica fora),
   não cancelado, sem garantia.
2. UMA nota de PRODUTO do pedido em `nf_nota` (≥ 60% do total, a régua do Pós
   Vendas). A de embalagem nunca vira garantia.
3. O CPF é o do destinatário DESSA nota (com dígito verificador). CNPJ, nota
   sem documento ou CPF inválido → pendência (cadastro manual, como hoje).
4. A mesma NF (CPF + número + série, ou a chave de 44 dígitos) já com garantia
   → pendência: o robô nunca cria a segunda.

O que não passou vira uma linha em `garantia_pendencias` (o motivo) e só é
relido depois de `REVER_PENDENCIA`. A data de entrega e os prazos seguem o
robô de sempre (`garantia.recalcular_todas`, de hora em hora).

As conversas entram por PONTEIRO (`garantia_conversas`), nunca por cópia:
  - `pedido`: a conversa tem o nº do pedido na plataforma (o `pedido_marketplace`
    da conversa ou, no ML, o pack/order do retrato);
  - `comprador`: Shopee/TikTok têm UMA conversa por comprador com todos os
    pedidos dele — o índice `atendimento_pedidos_comprador` diz de quem é o
    pedido. O painel corta as mensagens pela data da compra.
Nº de pedido curto (o sequencial do site, do presencial) não liga nada
sozinho: pode ser o número de outra compra em outra plataforma.

Liga/desliga: `settings.garantia_automatica` (GARANTIA_AUTOMATICA no .env).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

import structlog
from sqlalchemy import and_, exists, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import array as pg_array
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder
from app.models.atendimento import AtendimentoConversa, AtendimentoPedidoComprador
from app.models.garantia import (
    Garantia,
    GarantiaAtendimento,
    GarantiaConversa,
    GarantiaPendencia,
)
from app.models.nf import NfNota
from app.services import garantia as svc
from app.services.atendimento.agente.ferramentas import LINHA_URANYX, linha_do_item
from app.services.atendimento.constantes import PLATAFORMA_SITE
from app.services.bling_situacoes import (
    SITUACAO_AGUARDANDO_CANCELAMENTO_STR,
    SITUACAO_CANCELADO,
)
from app.services.pos_vendas import _FRACAO_PRODUTO_MIN

logger = structlog.get_logger()

ROTULO_ROBO = svc.ROTULO_GARANTIA_AUTOMATICA
# A `nf_nota` (o XML da nota de produto) começa em 19/08/2026: antes disso o
# DaVinci não tem a NF de produto de nenhum pedido (levantamento de 27/10).
INICIO_DAS_NOTAS = date(2026, 8, 19)
# A volta de rotina olha os pedidos destes últimos dias (a NF de produto sai
# em até ~4 dias; o pedido parado em "Aguardando cancelamento" volta depois).
JANELA_DIAS = 45
# Pedidos avaliados por volta (os mais novos primeiro). O retroativo chama em
# laço até acabar.
LOTE = 400
REVER_PENDENCIA = timedelta(hours=12)
# Cancelado e Aguardando cancelamento não viram garantia; o espelho marca
# "excluido" no pedido apagado do Bling.
SITUACOES_FORA = (str(SITUACAO_CANCELADO), SITUACAO_AGUARDANDO_CANCELAMENTO_STR, "excluido")
# Nº do pedido na plataforma com menos que isto não liga conversa sozinho.
MIN_NUMERO_PEDIDO = 8
# A pergunta do anúncio (pré-venda) não tem pedido; nunca entra.
CANAIS_FORA = ("pergunta",)
# Desde quando (para trás) a volta de rotina procura conversa nova/mexida.
JANELA_LIGACAO = timedelta(hours=3)
MOTIVO_CANCELADO = "pedido cancelado no Bling"


@dataclass
class Resultado:
    avaliados: int = 0
    criadas: int = 0
    pendencias: dict[str, int] = field(default_factory=dict)
    conversas_ligadas: int = 0
    descartadas: int = 0
    reativadas: int = 0
    pedidos: int = 0
    ids_criados: list[int] = field(default_factory=list)

    def como_dict(self) -> dict:
        return {
            "avaliados": self.avaliados,
            "criadas": self.criadas,
            "pendencias": dict(self.pendencias),
            "conversas_ligadas": self.conversas_ligadas,
            "descartadas": self.descartadas,
            "reativadas": self.reativadas,
            "pedidos": self.pedidos,
        }


@dataclass(frozen=True)
class Pendencia:
    motivo: str
    detalhe: str | None = None


MOTIVOS = {
    "pedido_sumiu": "o pedido não está mais no espelho do Bling",
    "cancelado": "pedido cancelado",
    "sem_produto_uranyx": "nenhum item é da linha Uranyx",
    "sem_nf_produto": "nenhuma NF de produto ligada ao pedido",
    "nf_produto_repetida": "mais de uma NF de produto no pedido",
    "nf_sem_cpf": "a NF de produto não tem CPF válido (CNPJ ou vazio)",
    "sem_nome": "a NF de produto não tem o nome do cliente",
    "nf_ja_tem_garantia": "esta NF já tem garantia",
    "colisao": "outra garantia foi gravada ao mesmo tempo",
}


# ── Quem vira garantia ──────────────────────────────────────────────────────


def _desde_padrao(hoje: date) -> date:
    return max(INICIO_DAS_NOTAS, hoje - timedelta(days=JANELA_DIAS))


async def candidatos(
    session: AsyncSession, desde: date, limite: int, *, agora: datetime | None = None
) -> list[str]:
    """Nº do Bling dos pedidos que PODEM virar garantia (os mais novos antes):
    com SKU do recorte Uranyx, fora de cancelado, com uma NF de ≥ 60% do total
    e sem garantia nem pendência recente. A régua fina é `avaliar`."""
    agora = agora or datetime.now(UTC)
    bo = BlingOrder
    ped = (
        select(
            bo.numero.label("numero"),
            func.max(bo.data).label("data"),
            func.max(bo.total).label("total"),
            func.max(func.coalesce(bo.situacao, "")).label("situacao"),
            func.bool_or(bo.item_codigo.op("~*")(svc.SKU_URANYX)).label("urx"),
        )
        .where(
            bo.numero.is_not(None),
            bo.data >= datetime.combine(desde, time(), svc.SAO_PAULO),
        )
        .group_by(bo.numero)
        .subquery()
    )
    q = (
        select(ped.c.numero)
        .where(
            ped.c.urx.is_(True),
            ped.c.situacao.notin_(SITUACOES_FORA),
            ped.c.total > 0,
            exists().where(
                NfNota.pedido_bling == ped.c.numero,
                NfNota.valor >= ped.c.total * _FRACAO_PRODUTO_MIN,
            ),
            ~exists().where(Garantia.pedido_bling == ped.c.numero),
            ~exists().where(
                GarantiaPendencia.pedido_bling == ped.c.numero,
                GarantiaPendencia.visto_em > agora - REVER_PENDENCIA,
            ),
        )
        .order_by(ped.c.data.desc().nulls_last(), ped.c.numero.desc())
        .limit(limite)
    )
    return [str(n) for n in (await session.execute(q)).scalars()]


async def avaliar(
    session: AsyncSession, pedido: svc.PedidoInfo | None
) -> tuple[svc.NotaDoPedido, str, str] | Pendencia:
    """(nota de produto, CPF, nome) do pedido — ou o motivo de não criar.
    Marca em cada item se ele é da linha Uranyx (a regra do agente)."""
    if pedido is None:
        return Pendencia("pedido_sumiu")
    if str(pedido.situacao_id or "") in SITUACOES_FORA:
        return Pendencia("cancelado", pedido.situacao)
    for item in pedido.itens:
        item["uranyx"] = linha_do_item(item.get("sku"), item.get("descricao")) == LINHA_URANYX
    if not any(i["uranyx"] for i in pedido.itens):
        skus = ", ".join(str(i.get("sku") or "?") for i in pedido.itens)[:200]
        return Pendencia("sem_produto_uranyx", skus)
    produtos = [n for n in pedido.notas if n.papel == "produto"]
    if not produtos:
        return Pendencia("sem_nf_produto")
    if len(produtos) > 1:
        return Pendencia("nf_produto_repetida", ", ".join(f"NF {n.numero}" for n in produtos))
    nota = produtos[0]
    cpf = svc.so_digitos(nota.destinatario_doc)
    if len(cpf) != 11 or not svc.cpf_valido(cpf):
        return Pendencia("nf_sem_cpf", f"NF {nota.numero}")
    nome = svc.normalizar_nome(nota.destinatario_nome) or (pedido.nome[0] or "")
    if len(nome) < 3:
        return Pendencia("sem_nome", f"NF {nota.numero}")
    existente = await _garantia_da_nf(session, cpf, nota)
    if existente is not None:
        return Pendencia("nf_ja_tem_garantia", f"garantia #{existente}")
    return nota, cpf, nome


async def _garantia_da_nf(session: AsyncSession, cpf: str, nota: svc.NotaDoPedido) -> int | None:
    """A garantia que já existe para esta NF (a RN06 do cadastro manual:
    série vazia casa com qualquer série; a chave de 44 dígitos também vale)."""
    serie_casa = (
        or_(Garantia.nf_serie == nota.serie, Garantia.nf_serie == "")
        if nota.serie
        else literal(True)
    )
    conds = [and_(Garantia.cpf == cpf, Garantia.nf_numero == nota.numero, serie_casa)]
    if nota.chave:
        conds.append(Garantia.nf_chave == nota.chave)
    return (await session.execute(select(Garantia.id).where(or_(*conds)).limit(1))).scalar()


async def _anotar_pendencia(session: AsyncSession, numero: str, p: Pendencia) -> None:
    ins = pg_insert(GarantiaPendencia).values(
        pedido_bling=numero, motivo=p.motivo, detalhe=p.detalhe, tentativas=1
    )
    await session.execute(
        ins.on_conflict_do_update(
            index_elements=[GarantiaPendencia.pedido_bling],
            set_={
                "motivo": ins.excluded.motivo,
                "detalhe": ins.excluded.detalhe,
                "visto_em": func.now(),
                "tentativas": GarantiaPendencia.tentativas + 1,
            },
        )
    )


async def criar_do_pedido(
    session: AsyncSession, numero: str, *, agora: datetime | None = None
) -> Garantia | Pendencia:
    """Avalia UM pedido e cria a garantia dele (num SAVEPOINT: a colisão de um
    pedido não desfaz o lote)."""
    pedido = await svc.pedido_pelo_numero_bling(session, numero)
    decisao = await avaliar(session, pedido)
    if isinstance(decisao, Pendencia):
        await _anotar_pendencia(session, numero, decisao)
        return decisao
    assert pedido is not None
    nota, cpf, nome = decisao
    entrega = await svc.resolver_entrega(session, pedido.numero, pedido.numeroloja)
    g = Garantia(origem="robo", criado_por=None, cliente_nome=nome, cpf=cpf)
    svc.aplicar_pedido(g, pedido, nota.numero, nota.serie, nota)
    svc.aplicar_entrega(g, entrega)
    g.entrega_verificada_em = agora or datetime.now(UTC)
    try:
        async with session.begin_nested():
            session.add(g)
            await session.flush()
    except IntegrityError:
        p = Pendencia("colisao", f"NF {nota.numero}")
        await _anotar_pendencia(session, numero, p)
        return p
    svc.registrar_log(
        session,
        g.id,
        "criou_automatico",
        None,
        detalhe=f"pedido {g.pedido_bling} · NF de produto {g.nf_numero}"
        + (f" série {g.nf_serie}" if g.nf_serie else "")
        + (f" · entrega {svc._br(g.data_inicio)}" if g.data_inicio else " · aguardando entrega"),
        robo=ROTULO_ROBO,
    )
    await session.execute(
        GarantiaPendencia.__table__.delete().where(GarantiaPendencia.pedido_bling == numero)
    )
    return g


# ── Conversas ───────────────────────────────────────────────────────────────


def _garantias_vivas(garantia_ids: list[int] | None):
    conds = [
        Garantia.descartada_em.is_(None),
        Garantia.pedido_marketplace.is_not(None),
        func.length(Garantia.pedido_marketplace) >= MIN_NUMERO_PEDIDO,
    ]
    if garantia_ids is not None:
        conds.append(Garantia.id.in_(garantia_ids))
    return conds


def _recente(desde: datetime | None, *colunas):
    if desde is None:
        return []
    return [or_(*[c >= desde for c in colunas])]


async def ligar_conversas(
    session: AsyncSession,
    *,
    garantia_ids: list[int] | None = None,
    desde: datetime | None = None,
) -> int:
    """Liga as conversas aos pedidos das garantias (ponteiro, sem cópia).
    `garantia_ids` = só estas; `desde` = só as conversas criadas/mexidas
    depois (a volta de rotina). Devolve quantas ligações NOVAS."""
    if garantia_ids is not None and not garantia_ids:
        return 0
    c, g, p = AtendimentoConversa, Garantia, AtendimentoPedidoComprador
    # O e-mail do site (SAC) só liga pelo CPF do formulário (garantia_sac): o
    # pedido citado sozinho pode ser o de outra pessoa (revisão de 28/10/2026).
    base_c = [c.canal.notin_(CANAIS_FORA), c.plataforma != PLATAFORMA_SITE]
    mexida_c = _recente(desde, c.created_at, c.updated_at, c.ultima_mensagem_em)
    chaves_ml = (
        select(
            c.id.label("conversa_id"),
            func.unnest(
                pg_array(
                    [
                        c.dados["pack_id"].astext,
                        c.dados["order_id"].astext,
                        c.dados[("pedido_mkt", "pedido")].astext,
                    ]
                )
            ).label("chave"),
        )
        .where(c.plataforma == "ml", *base_c, *mexida_c)
        .subquery()
    )
    consultas = [
        # 1) o nº do pedido na conversa
        select(g.id, c.id, literal("pedido"))
        .select_from(g)
        .join(c, c.pedido_marketplace == g.pedido_marketplace)
        .where(*_garantias_vivas(garantia_ids), *base_c, *mexida_c),
        # 2) ML: o pack/order do retrato (o Bling grava ora um, ora outro).
        # As chaves saem UMA vez por conversa (unnest) e a ligação é por
        # igualdade — vira hash join; o OR sobre o JSONB virava garantias ×
        # conversas (revisão de 28/10/2026).
        select(g.id, chaves_ml.c.conversa_id, literal("pedido"))
        .select_from(g)
        .join(chaves_ml, chaves_ml.c.chave == g.pedido_marketplace)
        .where(*_garantias_vivas(garantia_ids)),
        # 3) a conversa do comprador (Shopee/TikTok): o índice diz de quem é o pedido
        select(g.id, c.id, literal("comprador"))
        .select_from(g)
        .join(p, p.pedido == g.pedido_marketplace)
        .join(
            c,
            and_(
                c.integration_id == p.integration_id,
                c.comprador_id == p.comprador_id,
                c.plataforma == p.plataforma,
            ),
        )
        .where(
            *_garantias_vivas(garantia_ids),
            *base_c,
            *_recente(desde, c.created_at, c.updated_at, c.ultima_mensagem_em, p.atualizado_em),
        ),
    ]
    novas = 0
    for q in consultas:
        ins = (
            pg_insert(GarantiaConversa)
            .from_select(["garantia_id", "conversa_id", "como"], q)
            .on_conflict_do_nothing()
        )
        novas += (await session.execute(ins)).rowcount or 0
    return novas


# ── Cancelados ──────────────────────────────────────────────────────────────


async def descartar_canceladas(session: AsyncSession, *, agora: datetime | None = None) -> int:
    """A garantia DO ROBÔ cujo pedido foi cancelado no Bling antes de qualquer
    atendimento registrado sai da lista (não se apaga). A manual e a que já
    tem atendimento ficam como estão — quem decide é a pessoa."""
    agora = agora or datetime.now(UTC)
    ids = list(
        (
            await session.execute(
                select(Garantia.id).where(
                    Garantia.origem == "robo",
                    Garantia.descartada_em.is_(None),
                    exists().where(
                        BlingOrder.numero == Garantia.pedido_bling,
                        BlingOrder.situacao == str(SITUACAO_CANCELADO),
                    ),
                    ~exists().where(GarantiaAtendimento.garantia_id == Garantia.id),
                )
            )
        ).scalars()
    )
    if not ids:
        return 0
    await session.execute(
        update(Garantia)
        .where(Garantia.id.in_(ids))
        .values(descartada_em=agora, descartada_motivo=MOTIVO_CANCELADO)
    )
    for gid in ids:
        svc.registrar_log(
            session, gid, "descartou", None, detalhe="pedido cancelado no Bling", robo=ROTULO_ROBO
        )
    return len(ids)


async def reativar_descancelados(session: AsyncSession) -> int:
    """O cancelamento desfeito no Bling (ex.: o cancelamento indevido de
    23/09): a garantia que o robô descartou volta para a lista. Só a que ele
    mesmo descartou por cancelamento."""
    ids = list(
        (
            await session.execute(
                select(Garantia.id).where(
                    Garantia.origem == "robo",
                    Garantia.descartada_em.is_not(None),
                    Garantia.descartada_motivo == MOTIVO_CANCELADO,
                    exists().where(BlingOrder.numero == Garantia.pedido_bling),
                    ~exists().where(
                        BlingOrder.numero == Garantia.pedido_bling,
                        BlingOrder.situacao == str(SITUACAO_CANCELADO),
                    ),
                )
            )
        ).scalars()
    )
    if not ids:
        return 0
    await session.execute(
        update(Garantia)
        .where(Garantia.id.in_(ids))
        .values(descartada_em=None, descartada_motivo=None)
    )
    for gid in ids:
        svc.registrar_log(
            session,
            gid,
            "reativou",
            None,
            detalhe="o pedido saiu de Cancelado no Bling",
            robo=ROTULO_ROBO,
        )
    return len(ids)


# ── A volta ─────────────────────────────────────────────────────────────────


async def rodar(
    session: AsyncSession,
    *,
    desde: date | None = None,
    limite: int = LOTE,
    agora: datetime | None = None,
) -> Resultado:
    """Uma volta do robô: cria as garantias novas, liga as conversas (das novas
    e das conversas mexidas há pouco) e descarta as canceladas. Quem chama faz
    o commit."""
    agora = agora or datetime.now(UTC)
    r = Resultado()
    numeros = await candidatos(session, desde or _desde_padrao(svc.hoje()), limite, agora=agora)
    for numero in numeros:
        r.avaliados += 1
        resultado = await criar_do_pedido(session, numero, agora=agora)
        if isinstance(resultado, Pendencia):
            r.pendencias[resultado.motivo] = r.pendencias.get(resultado.motivo, 0) + 1
        else:
            r.criadas += 1
            r.ids_criados.append(resultado.id)
    r.conversas_ligadas += await ligar_conversas(session, garantia_ids=r.ids_criados)
    r.conversas_ligadas += await ligar_conversas(session, desde=agora - JANELA_LIGACAO)
    # O SAC do site (28/10/2026): o protocolo liga pelo CPF + NF/pedido do
    # formulário (services/garantia_sac.py).
    from app.services import garantia_sac

    r.conversas_ligadas += await garantia_sac.ligar_sac(session, desde=agora - JANELA_LIGACAO)
    r.descartadas = await descartar_canceladas(session, agora=agora)
    r.reativadas = await reativar_descancelados(session)
    # O pedido de garantia (28/10/2026): o Painel mostra só quem PEDIU
    # (mensagem, SAC, devolução por defeito) — services/garantia_pedido.py.
    from app.services import garantia_pedido

    r.pedidos = await garantia_pedido.marcar_pedidos(session, desde=agora - JANELA_LIGACAO)
    return r


async def retroativo(session: AsyncSession, *, desde: date = INICIO_DAS_NOTAS) -> Resultado:
    """A carga dos pedidos que já existem (desde que há NF de produto), em
    lotes com commit, mais a ligação de TODAS as conversas."""
    total = Resultado()
    while True:
        r = await rodar(session, desde=desde)
        await session.commit()
        total.avaliados += r.avaliados
        total.criadas += r.criadas
        total.conversas_ligadas += r.conversas_ligadas
        total.descartadas += r.descartadas
        total.pedidos += r.pedidos
        for k, v in r.pendencias.items():
            total.pendencias[k] = total.pendencias.get(k, 0) + v
        logger.info("garantia_auto_retroativo_lote", **r.como_dict())
        if r.avaliados < LOTE:
            break
    total.conversas_ligadas += await ligar_conversas(session)
    from app.services import garantia_sac

    total.conversas_ligadas += await garantia_sac.ligar_sac(session)
    from app.services import garantia_pedido

    total.pedidos += await garantia_pedido.marcar_pedidos(session)
    await session.commit()
    return total
