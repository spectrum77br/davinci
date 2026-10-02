"""O pedido por trás da conversa: o painel da direita e os FATOS da IA.

A pessoa que responde precisa ver, ao lado da conversa, o que o cliente
comprou, em que pé está o pedido no Bling, o rastreio, a NF e se já existe
chamado ou devolução — sem abrir quatro telas. A IA precisa exatamente do
mesmo, com uma regra a mais: o modelo NUNCA inventa rastreio, prazo ou NF;
o código tira daqui e preenche as lacunas (`ia.py`).

A chave é `conversa.pedido_marketplace` (o número do pedido NA PLATAFORMA —
é o que o adaptador sabe). `lookup_pedido` (services/chamados.py) acha o
pedido no espelho do Bling por `numero` OU `numeroloja`; daí saem o número
do Bling e o resto:

    {"pedido": {"numero", "numeroloja", "data", "situacao", "enviado_em",
                "itens": [{"descricao", "sku", "quantidade"}]} | None,
     "logistica": {"rastreio", "transportadora", "previsao", "entregue_em",
                   "status", "data_envio"} | None,
     "chamados": [{"id", "status", "titulo"}],     # só os NÃO resolvidos
     "devolucoes": [{"id", "status"}],
     "nota_fiscal": {"numero", "emitida_em"} | None,
     "outras_perguntas": [{"id", "texto", "situacao", "status", "respondida",
                           "em", "data"}],
     "reclamacoes": [{"id", "plataforma", "numero", "tipo", "tipo_rotulo",
                      "status", "aberta", "prazo_em", "acao_pendente",
                      "encerrada_em"}],
     "avaliacoes": [{"id", "plataforma", "estrelas", "pedido", "do_pedido",
                     "criado_em", "respondida", "pendente", "tratada",
                     "pode_responder"}]}

`reclamacoes` = as reclamações, mediações e devoluções DA PLATAFORMA
(`atendimento_reclamacoes`, a mesma ligação do cartão e da etiqueta), as
abertas primeiro (prazo mais curto antes). Era a lacuna do 297840: com a
mediação 5582543195 aberta no ML, o painel e a IA diziam "Devolução:
nenhuma" porque só liam a aba Devoluções. Só o que a plataforma diz (tipo,
status, prazo, ação esperada da loja) — nenhum texto do comprador.

`avaliacoes` = as avaliações de venda do pedido e as anteriores do mesmo
comprador (`services/atendimento/avaliacoes.py`, RF8 — 02/10/2026): a nota,
se foi respondida e se está PENDENTE. Nunca o texto da avaliação (é texto do
comprador: a aba ★ da tela lê da rota própria).

`outras_perguntas` é só da PERGUNTA do ML (uma conversa por pergunta): as
últimas 5 perguntas do MESMO comprador no MESMO anúncio, lidas do nosso
banco — quem responde vê que "ele já perguntou das medidas ontem" sem abrir
cinco conversas. É texto do comprador: vai para a TELA, nunca para o modelo
(a IA monta os fatos dela com outras chaves).

`enviado_em`, `data_envio` e `nota_fiscal` vão além do mínimo da spec: são
as lacunas `{data_envio}` e `{nf_numero}` da IA.

Sem NENHUM dado pessoal do comprador (nome, CPF, endereço, e-mail): este
dicionário vai para a tela, para o log do rascunho (`fatos`) e, em parte,
para o provedor do modelo. Datas em ISO (texto), para caber em JSON.

Nunca levanta. Cada bloco roda num SAVEPOINT: uma consulta que falha (tabela
de outro ambiente, dado torto) volta só ela — a transação de quem chamou
segue viva e a conversa abre com o que deu para achar.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    BlingNotaEmitida,
    BlingOrder,
    Chamado,
    ChamadoPedido,
    DevolucaoRastreio,
    Devolution,
    Logistica,
    NfNota,
)
from app.services.chamados import lookup_pedido

logger = structlog.get_logger()

# Tetos: o painel mostra poucos, e o prompt da IA não pode crescer à toa.
MAX_ITENS = 20
MAX_CHAMADOS = 10
MAX_DEVOLUCOES = 5
MAX_OUTRAS_PERGUNTAS = 5
MAX_RECLAMACOES = 5
MAX_AVALIACOES = 5
# Uma linha por pergunta no painel.
MAX_CHARS_PERGUNTA = 140

# Situações do Bling em que a NF-e existe de fato (autorizada, DANFE, registrada)
# — as mesmas do sync do Pós Vendas (services/pos_vendas.ISSUED_SITUACOES).
_NF_EMITIDA = (5, 6, 7)

# Status oficial do chamado (services/chamados.STATUS_*) em português de tela.
_STATUS_CHAMADO = {
    "em_analise": "em análise na plataforma",
    "prova": "plataforma pediu prova",
    "reembolso_pago": "reembolso pago pela plataforma",
    "ganhamos": "encerrado (ganhamos)",
    "perdemos": "encerrado (perdemos)",
    "encerrado": "encerrado pela plataforma",
    "aguardando": "aguardando a plataforma",
}

# De onde veio o chamado, como a tela escreve (com acento). O que não estiver
# aqui aparece como veio — melhor um nome cru que um nome errado.
_ORIGEM_CHAMADO = {
    "logistica": "Logística",
    "devolucao": "Devolução",
    "margem": "Margem",
}


def vazio() -> dict:
    """O contexto de uma conversa sem pedido (pergunta pré-venda, e-mail sem número)."""
    return {
        "pedido": None,
        "logistica": None,
        "chamados": [],
        "devolucoes": [],
        "nota_fiscal": None,
        "outras_perguntas": [],
        "reclamacoes": [],
        "avaliacoes": [],
    }


def _iso(valor: date | datetime | None) -> str | None:
    if valor is None:
        return None
    return valor.isoformat()


async def _seguro[T](
    session: AsyncSession,
    etapa: str,
    consulta: Callable[[], Awaitable[T]],
    padrao: T,
    conversa_id: Any,
) -> T:
    """Roda uma consulta num SAVEPOINT; erro vira `padrao` + log (sem PII)."""
    try:
        async with session.begin_nested():
            return await consulta()
    except Exception as e:  # noqa: BLE001 — contexto nunca derruba a tela nem a IA
        logger.warning(
            "atendimento_contexto_falhou",
            etapa=etapa,
            conversa_id=str(conversa_id),
            err=type(e).__name__,
        )
        return padrao


# ── Pedido ────────────────────────────────────────────────────────────────


async def _pedido(session: AsyncSession, numero_mkt: str) -> dict | None:
    info = await lookup_pedido(session, numero_mkt)
    if info is None:
        return None
    numero = info.get("pedido_bling")
    linhas = (
        (
            await session.execute(
                select(BlingOrder)
                .where(BlingOrder.numero == numero)
                .order_by(BlingOrder.item_index)
                .limit(MAX_ITENS)
            )
        )
        .scalars()
        .all()
    )
    itens = [
        {"descricao": r.item_descricao, "sku": r.item_codigo, "quantidade": r.item_quantidade}
        for r in linhas
        if r.item_descricao or r.item_codigo
    ]
    # `em_andamento_data` = dia em que o pedido foi para "Em andamento" no
    # Bling, que na operação é o despacho. Replicada em todas as linhas.
    enviado_em = next((r.em_andamento_data for r in linhas if r.em_andamento_data), None)
    return {
        "numero": numero,
        "numeroloja": info.get("pedido_marketplace"),
        "data": _iso(info.get("data")),
        "situacao": info.get("status_bling"),
        "enviado_em": _iso(enviado_em),
        "itens": itens,
    }


# ── Logística ─────────────────────────────────────────────────────────────


def _status_logistica(lg: Logistica) -> str | None:
    """Uma frase de estado do envio, da mais forte para a mais fraca."""
    if lg.entregue_em is not None:
        return "entregue"
    if lg.problema_correios:
        return f"ocorrência na transportadora: {lg.problema_correios}"
    if lg.localizacao:
        return lg.localizacao
    if lg.rastreio:
        return "postado"
    return None


async def _logistica(
    session: AsyncSession, numero_bling: str | None, numero_mkt: str
) -> dict | None:
    conds = [Logistica.pedido_marketplace == numero_mkt]
    if numero_bling:
        conds.append(Logistica.pedido_bling == numero_bling)
    lg = (
        await session.execute(
            select(Logistica).where(or_(*conds)).order_by(Logistica.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if lg is None:
        return None
    return {
        "rastreio": lg.rastreio,
        # O "serviço" do objeto de postagem do Bling (SEDEX, PAC, Logística
        # Amazon DBA...) é o que o cliente reconhece como transportadora.
        "transportadora": lg.servico_envio,
        # Previsão da transportadora; na Amazon, sem ela, o "entregar até".
        "previsao": _iso(lg.previsao_correios or lg.prazo_entrega_amazon),
        "entregue_em": _iso(lg.entregue_em),
        "status": _status_logistica(lg),
        "data_envio": _iso(lg.postagem_data),
    }


# ── Chamados e devoluções ─────────────────────────────────────────────────


async def _chamados(session: AsyncSession, numero_bling: str | None, numero_mkt: str) -> list[dict]:
    conds = [Chamado.pedido_marketplace == numero_mkt]
    if numero_bling:
        conds.append(Chamado.pedido_bling == numero_bling)
        # Chamado em LOTE (vários pedidos atrasados num chamado só).
        conds.append(
            Chamado.id.in_(
                select(ChamadoPedido.chamado_id).where(ChamadoPedido.pedido_bling == numero_bling)
            )
        )
    linhas = (
        (
            await session.execute(
                select(Chamado)
                .where(or_(*conds), Chamado.resolvido.is_(False))
                .order_by(Chamado.created_at.desc())
                .limit(MAX_CHAMADOS)
            )
        )
        .scalars()
        .all()
    )
    saida = []
    for ch in linhas:
        titulo = _ORIGEM_CHAMADO.get(ch.origem or "", ch.origem or "Chamado")
        if ch.chamado:
            titulo = f"{titulo} · nº {ch.chamado}"
        saida.append(
            {
                "id": str(ch.id),
                "status": _STATUS_CHAMADO.get(ch.status_plataforma or "", "aberto"),
                "titulo": titulo,
            }
        )
    return saida


def _status_devolucao(dv: Devolution) -> str:
    if dv.data_devolvido_estoque is not None or dv.devolver_estoque:
        return "devolvido ao estoque"
    if dv.reembolso:
        return "reembolso"
    return "em andamento"


async def _devolucoes(
    session: AsyncSession, numero_bling: str | None, numero_mkt: str
) -> list[dict]:
    conds = [Devolution.pedido_marketplace == numero_mkt]
    if numero_bling:
        conds.append(Devolution.pedido_bling == numero_bling)
    linhas = (
        (
            await session.execute(
                select(Devolution)
                .where(or_(*conds))
                .order_by(Devolution.created_at.desc())
                .limit(MAX_DEVOLUCOES)
            )
        )
        .scalars()
        .all()
    )
    saida = [{"id": str(dv.id), "status": _status_devolucao(dv)} for dv in linhas]
    if not saida and numero_bling:
        # O pacote de VOLTA existe antes de a devolução ser lançada (sync da
        # returns API): sem olhar aqui, "nenhuma devolução" esconderia um
        # pacote voltando — e a IA responderia como se nada estivesse
        # acontecendo.
        rastreio = (
            await session.execute(
                select(DevolucaoRastreio).where(DevolucaoRastreio.pedido_bling == numero_bling)
            )
        ).scalar_one_or_none()
        if rastreio is not None and (
            rastreio.devolucao_status_auto or rastreio.rastreio or rastreio.rastreio_auto
        ):
            saida.append(
                {
                    "id": f"rastreio:{numero_bling}",
                    "status": rastreio.devolucao_status_auto or "pacote voltando",
                }
            )
    return saida


# ── Nota fiscal ───────────────────────────────────────────────────────────


async def _nota_fiscal(
    session: AsyncSession, numero_bling: str | None, numero_mkt: str
) -> dict | None:
    if numero_bling:
        nota = (
            await session.execute(
                select(NfNota)
                .where(NfNota.pedido_bling == numero_bling)
                .order_by(NfNota.data_emissao.desc().nulls_last())
                .limit(1)
            )
        ).scalar_one_or_none()
        if nota is not None:
            return {"numero": nota.numero, "emitida_em": _iso(nota.data_emissao)}
    # Espelho das contas de emissão do Bling: o fluxo de emissão grava o
    # número do marketplace no complemento do endereço — 1ª chave de
    # casamento do Pós Vendas.
    emitida = (
        await session.execute(
            select(BlingNotaEmitida)
            .where(
                BlingNotaEmitida.complemento == numero_mkt,
                BlingNotaEmitida.situacao.in_(_NF_EMITIDA),
            )
            .order_by(BlingNotaEmitida.data_emissao.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()
    if emitida is not None and emitida.numero:
        return {"numero": emitida.numero, "emitida_em": _iso(emitida.data_emissao)}
    return None


# ── Reclamações da plataforma ─────────────────────────────────────────────


async def _reclamacoes(session: AsyncSession, conversa: AtendimentoConversa) -> list[dict]:
    """As reclamações/mediações/devoluções da plataforma ligadas à conversa (ver o topo)."""
    # Import tardio: `reclamacoes` → `etiqueta_fatos` → este módulo.
    from app.services.atendimento import reclamacoes as reclamacoes_svc

    linhas = await reclamacoes_svc.reclamacoes_da_conversa(
        session, conversa, limite=MAX_RECLAMACOES
    )
    saida = []
    for r in linhas:
        tela = reclamacoes_svc.para_tela(r)
        saida.append(
            {
                "id": str(r.id),
                "plataforma": tela["plataforma_nome"],
                "numero": tela["numero"],
                "tipo": tela["tipo"],
                "tipo_rotulo": tela["tipo_rotulo"],
                "status": tela["status_rotulo"],
                "aberta": tela["aberta"],
                "prazo_em": _iso(tela["prazo_em"]),
                "acao_pendente": tela["acao_pendente"],
                "encerrada_em": _iso(tela["encerrada_em"]),
            }
        )
    return saida


# ── Avaliações de venda (RF8) ─────────────────────────────────────────────


async def _avaliacoes(session: AsyncSession, conversa: AtendimentoConversa) -> list[dict]:
    """As avaliações da conversa: nota e estado, SEM o texto (ver o topo)."""
    # Import tardio: `avaliacoes` → `etiqueta_fatos` → este módulo.
    from app.services.atendimento import avaliacoes as avaliacoes_svc

    return (await avaliacoes_svc.resumo_para_contexto(session, conversa))[:MAX_AVALIACOES]


# ── Outras perguntas do mesmo comprador (ML) ───────────────────────────────


def _chave_pergunta(conversa: AtendimentoConversa) -> tuple[str, str] | None:
    """(anúncio, comprador) da pergunta — colunas, com `dados` de reserva."""
    dados = conversa.dados if isinstance(conversa.dados, dict) else {}
    item = str(conversa.anuncio_id or dados.get("item_id") or "").strip()
    comprador = str(conversa.comprador_id or dados.get("from_id") or "").strip()
    if not item or not comprador:
        return None
    return item, comprador


async def _outras_perguntas(session: AsyncSession, conversa: AtendimentoConversa) -> list[dict]:
    chave = _chave_pergunta(conversa)
    if chave is None:
        return []
    item, comprador = chave
    item_col = func.coalesce(
        AtendimentoConversa.anuncio_id, AtendimentoConversa.dados["item_id"].astext
    )
    comprador_col = func.coalesce(
        AtendimentoConversa.comprador_id, AtendimentoConversa.dados["from_id"].astext
    )
    outras = (
        (
            await session.execute(
                select(AtendimentoConversa)
                .where(
                    AtendimentoConversa.id != conversa.id,
                    AtendimentoConversa.plataforma == conversa.plataforma,
                    AtendimentoConversa.canal == conversa.canal,
                    AtendimentoConversa.integration_id == conversa.integration_id,
                    item_col == item,
                    comprador_col == comprador,
                )
                .order_by(
                    AtendimentoConversa.ultima_do_cliente_em.desc().nulls_last(),
                    AtendimentoConversa.created_at.desc(),
                )
                .limit(MAX_OUTRAS_PERGUNTAS)
            )
        )
        .scalars()
        .all()
    )
    if not outras:
        return []
    ids = [c.id for c in outras]
    momento = func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
    # A pergunta = a primeira mensagem do cliente da conversa.
    perguntas: dict[Any, str | None] = {}
    for conversa_id, texto in (
        await session.execute(
            select(AtendimentoMensagem.conversa_id, AtendimentoMensagem.texto)
            .where(
                AtendimentoMensagem.conversa_id.in_(ids),
                AtendimentoMensagem.autor == "cliente",
            )
            .order_by(AtendimentoMensagem.conversa_id, momento.asc())
        )
    ).all():
        perguntas.setdefault(conversa_id, texto)
    respondidas = set(
        (
            await session.execute(
                select(AtendimentoMensagem.conversa_id).where(
                    AtendimentoMensagem.conversa_id.in_(ids),
                    AtendimentoMensagem.autor == "loja",
                    AtendimentoMensagem.status != "falhou",
                )
            )
        ).scalars()
    )
    saida = []
    for c in outras:
        texto = " ".join((perguntas.get(c.id) or "").split())
        if len(texto) > MAX_CHARS_PERGUNTA:
            texto = texto[: MAX_CHARS_PERGUNTA - 1].rstrip() + "…"
        dados = c.dados if isinstance(c.dados, dict) else {}
        em = _iso(c.ultima_do_cliente_em or c.created_at)
        saida.append(
            {
                "id": str(c.id),
                "texto": texto or None,
                "situacao": c.situacao,
                # `status` (o do ML: UNANSWERED, ANSWERED, CLOSED_UNANSWERED...)
                # e `data` são os nomes que a tela lê (AtendimentoPedido.vue).
                "status": str(dados.get("status_ml") or "") or None,
                "respondida": c.id in respondidas,
                "em": em,
                "data": em,
            }
        )
    return saida


# ── Entrada ───────────────────────────────────────────────────────────────


def _chaves_do_pedido(conversa: AtendimentoConversa) -> list[str]:
    """Números pelos quais o pedido pode estar no espelho do Bling, na ordem.

    Primeiro `pedido_marketplace`. Depois, no pós-venda do ML, o `pack_id` e
    o `order_id` que o adaptador guarda em `dados`: no pedido de carrinho o
    Bling às vezes grava o PACK em `numeroloja`, e o adaptador grava o ORDER
    em `pedido_marketplace` — sem a segunda chave, a conversa abriria sem
    pedido e a IA mandaria tudo para pessoa ("pedido não encontrado").

    E, no ML, o ORDER do retrato do pedido (`dados.pedido_mkt.pedido`, que o
    enriquecimento acha pelo `/packs`): em produção (01/10/2026) o pack NUNCA
    traz `order_id` à vista — as 147 conversas do pós-venda têm
    `pedido_marketplace` = pack, e em 53 delas (carrinho) o order é outro
    número. Sem esta chave, a reclamação do ML (gravada pelo ORDER) não
    chegava à conversa do comprador, e em 14 delas o Bling (que só tinha o
    order em `numeroloja`) nem ligava. No carrinho com vários pedidos o
    retrato mostra o próprio pack (nada a acrescentar): aí a reclamação casa
    pelo `pack_id` dela (`etiqueta_fatos.condicao_do_pedido`).
    """
    dados = conversa.dados if isinstance(conversa.dados, dict) else {}
    brutos = [conversa.pedido_marketplace, dados.get("pack_id"), dados.get("order_id")]
    if conversa.plataforma == "ml":
        retrato = dados.get("pedido_mkt")
        if isinstance(retrato, dict) and isinstance(retrato.get("pedido"), str | int):
            brutos.append(retrato.get("pedido"))
    chaves: list[str] = []
    for bruto in brutos:
        chave = str(bruto or "").strip()
        if chave and chave not in chaves:
            chaves.append(chave)
    return chaves


async def contexto_da_conversa(session: AsyncSession, conversa: AtendimentoConversa) -> dict:
    """Pedido, logística, chamados, devoluções e NF da conversa. Nunca levanta.

    Sem `pedido_marketplace` (pergunta pré-venda do ML, e-mail da Amazon sem
    número) devolve o pedido vazio sem ir ao banco — na pergunta do ML, só
    `outras_perguntas`. O resto (logística, chamados, devoluções, NF) casa
    pelo número com que o pedido foi ACHADO.
    """
    ctx = vazio()
    if conversa.plataforma == "ml" and conversa.canal == "pergunta":
        ctx["outras_perguntas"] = await _seguro(
            session, "outras_perguntas", lambda: _outras_perguntas(session, conversa), [],
            conversa.id,
        )
    if conversa.plataforma in ("shopee", "ml") and conversa.canal != "pergunta":
        # Pelo pedido OU pelo comprador: entra também sem pedido na conversa.
        ctx["avaliacoes"] = await _seguro(
            session, "avaliacoes", lambda: _avaliacoes(session, conversa), [], conversa.id
        )
    if not (conversa.pedido_marketplace or "").strip():
        return ctx
    chaves = _chaves_do_pedido(conversa)
    numero_mkt = chaves[0]
    cid = conversa.id

    for chave in chaves:
        pedido = await _seguro(
            session, "pedido", lambda chave=chave: _pedido(session, chave), None, cid
        )
        if pedido is not None:
            ctx["pedido"] = pedido
            numero_mkt = chave
            break
    numero_bling = (ctx["pedido"] or {}).get("numero")
    ctx["logistica"] = await _seguro(
        session, "logistica", lambda: _logistica(session, numero_bling, numero_mkt), None, cid
    )
    ctx["chamados"] = await _seguro(
        session, "chamados", lambda: _chamados(session, numero_bling, numero_mkt), [], cid
    )
    ctx["devolucoes"] = await _seguro(
        session, "devolucoes", lambda: _devolucoes(session, numero_bling, numero_mkt), [], cid
    )
    ctx["nota_fiscal"] = await _seguro(
        session, "nota_fiscal", lambda: _nota_fiscal(session, numero_bling, numero_mkt), None, cid
    )
    ctx["reclamacoes"] = await _seguro(
        session, "reclamacoes", lambda: _reclamacoes(session, conversa), [], cid
    )
    return ctx
