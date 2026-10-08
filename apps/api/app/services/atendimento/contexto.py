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
                "itens": [{"descricao", "sku", "quantidade"}],
                # só em "Aguardando Cancelamento" (83955) — item 4:
                "ag_cancelamento": {"codigo", "fala_cancelamento", "texto_ia"}}
               | None,
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

`pedido.ag_cancelamento` (item 4, 02/10/2026) = o PORQUÊ do pedido em
"Aguardando Cancelamento" (`ag_cancelamento.classificar`, com os fatos da NF
de `etiqueta_fatos.pedido_bling` e o status na plataforma pelo retrato da
conversa): o código, se pode falar em cancelamento e o texto do vocabulário
fechado da IA. Só existe em 83955 (os outros pedidos não pagam consulta).
Se a classificação falhar, fecha do lado SEGURO (`desconhecido`, sem falar
em cancelamento) sem derrubar o resto do pedido. A MÁSCARA ("em
processamento" no lugar da trava da Margem) é da IA
(`ia._fatos_para_o_modelo`), não daqui: este dicionário também vai para a
tela.

Sem NENHUM dado pessoal do comprador (nome, CPF, endereço, e-mail): este
dicionário vai para a tela, para o log do rascunho (`fatos`) e, em parte,
para o provedor do modelo. Datas em ISO (texto), para caber em JSON.

Nunca levanta. Cada bloco roda num SAVEPOINT: uma consulta que falha (tabela
de outro ambiente, dado torto) volta só ela — a transação de quem chamou
segue viva e a conversa abre com o que deu para achar.

GARANTIA PARA A IA (07/10/2026, combinado com o dono: "quando perguntarem de
validade, a IA vai puxar ali na tabela e dizer se tem validade ou não").
`garantia_para_ia` consulta o Painel de Garantia (`garantias`, cadastrada à
mão) e devolve o que a IA pode dizer: status, início (e de onde veio a
data), fim do hardware e do software — NADA de CPF nem nome. Não entra no
`contexto_da_conversa` (que vai para a tela): a tela tem o bloco próprio,
com permissão e escopo por equipe (`routers/garantias.situacao_da_conversa`).
Acha a garantia pelo pedido da conversa, pelo CPF do pedido (outras compras
do mesmo comprador) e pelo pedido/NF que o COMPRADOR citou nas mensagens
recentes. O CPF só serve para achar. NUNCA a garantia de outro comprador por
um número que se chuta: garantia de OUTRO CPF só entra pelo pedido da
própria conversa ou pelo número LONGO de marketplace que o comprador citou
(ML, TikTok, Amazon, Shopee, Temu — não se adivinha). NF e nº curto do Bling
(sequenciais, fáceis de chutar) só valem com o CPF da conversa conhecido (o
do pedido da conversa casado pelo nº do Bling, ou o da garantia do pedido) e
se forem desse mesmo CPF. O CPF que vem SÓ da nota emitida (casada pelo
complemento do endereço = nº do marketplace) vale só quando esse
complemento tem notas de um CPF só. CPF digitado pelo comprador não acha nada: não se
puxa a garantia de alguém digitando o CPF dele. Sem CPF da conversa, só o
pedido da conversa e o nº longo citado.
"""

from __future__ import annotations

import re
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
    Garantia,
    Logistica,
    NfNota,
)
from app.services import garantia as garantia_svc
from app.services.atendimento import ag_cancelamento
from app.services.bling_situacoes import SITUACAO_AGUARDANDO_CANCELAMENTO_STR
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


async def _ag_cancelamento(
    session: AsyncSession, numero: str, status_plataforma: str | None
) -> dict:
    """O porquê do pedido em 83955: `{codigo, fala_cancelamento, texto_ia}`. Nunca levanta.

    O pedido com a trilha da Margem e os fatos da NF vem de
    `etiqueta_fatos.pedido_bling` — import tardio: `etiqueta_fatos` importa
    este módulo. SAVEPOINT e `try` PRÓPRIOS: se a classificação falhar (ou
    não reconhecer o pedido como 83955), fecha do lado SEGURO
    (`desconhecido`, sem falar em cancelamento) e o resto do pedido segue —
    sem isto, o `_seguro` do pedido sumiria com o pedido inteiro.
    """
    motivo = None
    try:
        async with session.begin_nested():
            from app.services.atendimento import etiqueta_fatos

            pedido = await etiqueta_fatos.pedido_bling(session, numero)
            if pedido is not None and pedido.numero == numero:
                motivo = ag_cancelamento.classificar(pedido, status_plataforma=status_plataforma)
    except Exception as e:  # noqa: BLE001 — o motivo nunca derruba o pedido
        logger.warning(
            "atendimento_contexto_falhou",
            etapa="ag_cancelamento",
            pedido_bling=numero,
            err=type(e).__name__,
        )
        motivo = None
    if motivo is None:
        return {"codigo": ag_cancelamento.DESCONHECIDO, "fala_cancelamento": False}
    return {
        "codigo": motivo.codigo,
        "fala_cancelamento": motivo.fala_cancelamento,
        "texto_ia": motivo.texto_ia,
    }


async def _pedido(
    session: AsyncSession, numero_mkt: str, *, status_plataforma: str | None = None
) -> dict | None:
    """O pedido do Bling pelo número; `status_plataforma` = o do retrato da conversa.

    Em "Aguardando Cancelamento" (83955), o bloco `ag_cancelamento` com o
    porquê (item 4) — os outros pedidos não fazem consulta a mais.
    """
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
    saida = {
        "numero": numero,
        "numeroloja": info.get("pedido_marketplace"),
        "data": _iso(info.get("data")),
        "situacao": info.get("status_bling"),
        "enviado_em": _iso(enviado_em),
        "itens": itens,
    }
    # O id cru do espelho (o `status_bling` acima pode ser o NOME da situação).
    if any((r.situacao or "").strip() == SITUACAO_AGUARDANDO_CANCELAMENTO_STR for r in linhas):
        saida["ag_cancelamento"] = await _ag_cancelamento(session, numero, status_plataforma)
    return saida


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


# ── Garantia (Painel de Garantia Uranyx) para a IA ────────────────────────

# Quantas garantias vão para o modelo (as do pedido da conversa primeiro).
MAX_GARANTIAS_IA = 3
MAX_CHARS_PRODUTO_GARANTIA = 60
# Teto do que se procura por citação (uma mensagem colada com 50 números não
# vira 50 consultas).
MAX_CITADOS = 5
# Teto das garantias lidas por consulta (as mais novas primeiro).
MAX_ACHADAS = 50

DE_ONDE_PEDIDO = "pedido desta conversa"
DE_ONDE_CITADA = "pedido ou NF que o comprador citou na conversa"
DE_ONDE_MESMO_COMPRADOR = "outra compra do mesmo comprador"
_ORDEM_DE_ONDE = {
    DE_ONDE_PEDIDO: 0,
    DE_ONDE_CITADA: 1,
    DE_ONDE_MESMO_COMPRADOR: 2,
}

# Status além dos do painel: sem data de entrega, mas o Bling já dá o pedido
# como entregue (`garantia.entregue_sem_data`).
STATUS_ENTREGUE_SEM_DATA = "entregue_sem_data"
STATUS_GARANTIA_IA = {
    **garantia_svc.STATUS_ROTULOS,
    STATUS_ENTREGUE_SEM_DATA: "Entregue sem data no DaVinci",
}
# Os que COBREM alguma coisa hoje (o validador barra promessa de cobertura
# sem um destes no bloco).
STATUS_COM_COBERTURA = (garantia_svc.STATUS_ATIVA, garantia_svc.STATUS_SOMENTE_SOFTWARE)

# De onde veio a data de início, em palavras que o modelo pode repetir — o
# rótulo do painel fala no "DaVinci" e em 17track.
_ORIGEM_DA_DATA_IA = {
    "ml": "data oficial da entrega no Mercado Livre",
    "shopee": "evento de entrega da transportadora na Shopee",
    "tiktok": "pedido entregue no TikTok Shop",
    "amazon": "entrega registrada na Amazon (EasyShip)",
    garantia_svc.FONTE_RASTREIO: "rastreio da transportadora (data aproximada)",
}

# O que o COMPRADOR cita (texto cru do banco, na forma de busca: minúsculo e
# sem acento). Formatos de produção (07/10/2026): Bling 5–6 dígitos (hoje
# 2xxxxx–3xxxxx), ML/Magalu/AliExpress 16 dígitos, TikTok 18, Amazon
# 3-7-7, Shopee AAMMDD + 8 letras/dígitos, Temu PO-999-…, NF 1 a 4 dígitos
# (com zeros à esquerda e pontos no DANFE: 000.001.234).
# Entre a palavra e o número, só o que se escreve ali ("nº", "número", "#",
# ":", "é"): "nota fiscal de 2 aparelhos" e "pedido de 10 unidades" não são
# número de nota nem de pedido.
_ENTRE = r"\s*(?:(?:n[o°]?\.?|numero|num\.?|e|eh|#|:|-)\s*){0,3}"
# O nº do Bling é curto e se confunde com qualquer número: só depois da palavra.
_RX_PEDIDO_BLING = re.compile(
    rf"\b(?:pedido|compra|venda)s?\b{_ENTRE}(?<![\w.])(\d{{5,6}})(?!\w|[.,]\d)"
)
_RX_PEDIDO_AMAZON = re.compile(r"(?<![\d-])\d{3}-\d{7}-\d{7}(?![\d-])")
_RX_PEDIDO_TEMU = re.compile(r"\bpo-\d{3}-\d{8,20}\b")
_RX_PEDIDO_SHOPEE = re.compile(r"(?<![a-z0-9])\d{6}[a-z0-9]{8}(?![a-z0-9])")
_RX_PEDIDO_LONGO = re.compile(r"(?<![\d-])(?:\d{18}|\d{16})(?![\d-])")
# NF de 1 dígito existe (15 em produção), mas "NF 2" é mais vezes outra coisa.
_RX_NF = re.compile(
    rf"\b(?:nf-?e?|nfs?|nota\s+fiscal|danfe)\b{_ENTRE}"
    r"(?<![\d.])(\d{1,3}(?:\.\d{3}){1,2}|\d{2,9})(?!\w|[.,]\d)"
)
_RX_CPF = re.compile(r"(?<![\d.\-/])\d{3}[.\s]?\d{3}[.\s]?\d{3}[-.\s]?\d{2}(?![\d\-/])")


def _forma_de_busca(texto: str | None) -> str:
    # Import tardio: o validador é do lote do cérebro (e importa constantes).
    from app.services.atendimento.validador import plano_de

    return plano_de(texto or "")


def citacoes_do_comprador(textos: list[str | None]) -> dict[str, list[str]]:
    """Pedidos, NFs e CPFs (válidos) que o comprador escreveu, na ordem, sem repetir.

    `pedidos` = como escritos (a Shopee em maiúsculas); `nfs` = só dígitos,
    sem zero à esquerda; `cpfs` = 11 dígitos com dígito verificador certo.
    Um número de pedido que também casa como CPF conta só como pedido. O
    CPF citado NÃO acha garantia (`garantia_para_ia` não o usa): fica aqui
    só para o texto do comprador ser lido inteiro, sem virar pedido nem NF.
    """
    pedidos: list[str] = []
    nfs: list[str] = []
    cpfs: list[str] = []

    def _por(lista: list[str], valor: str) -> None:
        if valor and valor not in lista and len(lista) < MAX_CITADOS:
            lista.append(valor)

    for bruto in textos:
        plano = _forma_de_busca(bruto)
        if not plano:
            continue
        for m in _RX_PEDIDO_AMAZON.finditer(plano):
            _por(pedidos, m.group(0))
        for m in _RX_PEDIDO_TEMU.finditer(plano):
            _por(pedidos, m.group(0).upper())
        for m in _RX_PEDIDO_SHOPEE.finditer(plano):
            if re.search(r"[a-z]", m.group(0)[6:]):
                _por(pedidos, m.group(0).upper())
        for m in _RX_PEDIDO_LONGO.finditer(plano):
            _por(pedidos, m.group(0))
        for m in _RX_PEDIDO_BLING.finditer(plano):
            _por(pedidos, m.group(1))
        for m in _RX_NF.finditer(plano):
            _por(nfs, garantia_svc.normalizar_nf(m.group(1)))
        for m in _RX_CPF.finditer(plano):
            digitos = garantia_svc.so_digitos(m.group(0))
            if digitos not in pedidos and garantia_svc.cpf_valido(digitos):
                _por(cpfs, digitos)
    return {"pedidos": pedidos, "nfs": nfs, "cpfs": cpfs}


# O nº do Bling (5–6 dígitos) é sequencial: qualquer um chuta um vizinho.
_BLING_CURTO = re.compile(r"\d{5,6}")


def _de_onde(
    g: Garantia,
    *,
    numero_bling: str | None,
    chaves: list[str],
    longos_citados: set[str],
    bling_citados: set[str],
    nfs_citadas: set[str],
    cpf_conversa: str | None,
) -> str | None:
    """Por que a garantia entra no bloco (None = não entra).

    De QUALQUER CPF, só duas portas: o pedido da própria conversa e os
    números LONGOS de marketplace que o comprador citou (`longos_citados`,
    com o nº do Bling deles) — ninguém adivinha um pedido de 16 dígitos;
    quem cita tem o pedido em mãos. O resto (nº curto do Bling citado, NF
    citada, outras compras) só do MESMO CPF da conversa: a NF tem 1 a 4
    dígitos e se repete entre compradores (2 séries, 3 emitentes), e o nº do
    Bling é sequencial — um dígito trocado cai na compra de outra pessoa.
    Sem o CPF da conversa, nada disso entra.
    """
    if (numero_bling and g.pedido_bling == numero_bling) or (
        g.pedido_marketplace and g.pedido_marketplace in chaves
    ):
        return DE_ONDE_PEDIDO
    if g.pedido_bling in longos_citados or (
        g.pedido_marketplace and g.pedido_marketplace in longos_citados
    ):
        return DE_ONDE_CITADA
    if cpf_conversa is None or g.cpf != cpf_conversa:
        return None
    if g.pedido_bling in bling_citados or (g.nf_numero or "") in nfs_citadas:
        return DE_ONDE_CITADA
    return DE_ONDE_MESMO_COMPRADOR


def _produto(g: Garantia) -> str | None:
    for item in g.itens or []:
        if isinstance(item, dict) and (desc := " ".join(str(item.get("descricao") or "").split())):
            if len(desc) > MAX_CHARS_PRODUTO_GARANTIA:
                desc = desc[: MAX_CHARS_PRODUTO_GARANTIA - 1].rstrip() + "…"
            return desc
    return None


async def garantia_para_ia(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    textos_cliente: list[str | None],
    pedido: dict | None,
) -> dict:
    """As garantias que a IA pode citar nesta conversa (ver o topo).

    `textos_cliente` = as mensagens recentes do comprador (texto cru: é só
    para achar pedido/NF citado — nada dele volta). `pedido` = o
    `ctx["pedido"]` do `contexto_da_conversa` (o nº do Bling que o pedido da
    conversa achou). Devolve {"consultada": True, "garantias": [...]}, cada
    uma {id, de_onde, status, status_rotulo, inicio, inicio_origem,
    fim_hardware, fim_software, produto} — datas ISO, SEM CPF e SEM nome.
    Lista vazia = sem garantia cadastrada. Quem chama roda num SAVEPOINT
    (`_seguro`): erro vira {"consultada": False, "garantias": []}.
    """
    # Tabela vazia (o começo: as garantias são cadastradas à mão): nada a
    # procurar — poupa as consultas do pedido e do CPF a cada sugestão.
    if await session.scalar(select(Garantia.id).limit(1)) is None:
        return {"consultada": True, "garantias": []}
    chaves = _chaves_do_pedido(conversa) if (conversa.pedido_marketplace or "").strip() else []
    numero_bling = str((pedido or {}).get("numero") or "").strip() or None
    citados = citacoes_do_comprador(textos_cliente)
    do_pedido = []
    if numero_bling:
        do_pedido.append(Garantia.pedido_bling == numero_bling)
    if chaves:
        do_pedido.append(Garantia.pedido_marketplace.in_(chaves))

    # O CPF da conversa = o do pedido (NF de produto, contato do Bling, nota
    # emitida); sem ele, o da garantia do pedido da conversa — só para achar
    # e comparar; nunca sai daqui. Só o pedido casado pelo NÚMERO do Bling:
    # `buscar_pedido` também casa pelo nº do marketplace (`numeroloja`), e um
    # pedido mais novo com aquele `numeroloja` é outra compra, de outra
    # pessoa. O CPF que o comprador DIGITA não entra: ninguém puxa a garantia
    # de outra pessoa digitando o CPF dela.
    cpf_conversa = None
    if numero_bling:
        info = await garantia_svc.buscar_pedido(session, numero_bling)
        if info is not None and info.numero == numero_bling:
            cpf_conversa = info.cpf
            so_da_nota_emitida = (
                cpf_conversa is not None
                and cpf_conversa == garantia_svc.so_digitos(info.cpf_nota_emitida)
                and cpf_conversa
                not in {
                    garantia_svc.so_digitos(info.documento),
                    garantia_svc.so_digitos(
                        info.nota_produto.destinatario_doc if info.nota_produto else None
                    ),
                }
            )
            if so_da_nota_emitida:
                # A nota emitida casa pelo COMPLEMENTO do endereço (= numeroloja):
                # com notas de 2+ CPFs nele, não se sabe de quem é o pedido.
                n_cpfs = await session.scalar(
                    select(func.count(func.distinct(BlingNotaEmitida.cpf_dest))).where(
                        BlingNotaEmitida.complemento == info.numeroloja,
                        BlingNotaEmitida.cpf_dest.is_not(None),
                    )
                )
                if n_cpfs != 1:
                    cpf_conversa = None
    if cpf_conversa is None and do_pedido:
        cpf_conversa = await session.scalar(
            select(Garantia.cpf).where(or_(*do_pedido)).order_by(Garantia.id.desc()).limit(1)
        )

    # Pedido citado: o nº curto do Bling à parte (só do mesmo CPF, ver
    # `_de_onde`); o longo do marketplace também pelo nº do Bling dele (o
    # pack/order do ML, que o Bling grava em `numeroloja`).
    bling_citados = {p for p in citados["pedidos"] if _BLING_CURTO.fullmatch(p)}
    longos_citados = set(citados["pedidos"]) - bling_citados
    if longos_citados:
        longos_citados |= {
            str(n)
            for n in (
                await session.execute(
                    select(BlingOrder.numero)
                    .where(
                        BlingOrder.numeroloja.in_(longos_citados),
                        BlingOrder.numero.is_not(None),
                    )
                    .distinct()
                    .limit(MAX_CITADOS * 2)
                )
            ).scalars()
        }
    nfs_citadas = set(citados["nfs"])

    # Só o que PODE entrar sai do banco: o pedido da conversa, o nº longo
    # citado e — com o CPF da conversa — as compras desse CPF (que já trazem
    # o nº curto do Bling e a NF citados, quando são dele).
    conds = list(do_pedido)
    if longos_citados:
        conds.append(Garantia.pedido_bling.in_(longos_citados))
        conds.append(Garantia.pedido_marketplace.in_(longos_citados))
    if cpf_conversa:
        conds.append(Garantia.cpf == cpf_conversa)
    if not conds:
        return {"consultada": True, "garantias": []}
    achadas = list(
        (
            await session.execute(
                select(Garantia).where(or_(*conds)).order_by(Garantia.id.desc()).limit(MAX_ACHADAS)
            )
        ).scalars()
    )
    escolhidas: dict[int, str] = {}
    for g in achadas:
        motivo = _de_onde(
            g,
            numero_bling=numero_bling,
            chaves=chaves,
            longos_citados=longos_citados,
            bling_citados=bling_citados,
            nfs_citadas=nfs_citadas,
            cpf_conversa=cpf_conversa,
        )
        if motivo is not None:
            escolhidas[g.id] = motivo

    por_id = {g.id: g for g in achadas}
    ordem = sorted(escolhidas, key=lambda i: (_ORDEM_DE_ONDE[escolhidas[i]], -i))
    garantias = [por_id[i] for i in ordem[:MAX_GARANTIAS_IA]]
    situacoes = await garantia_svc.situacoes_dos_pedidos(
        session, [g.pedido_bling for g in garantias]
    )
    dia = garantia_svc.hoje()
    saida = []
    for g in garantias:
        status = garantia_svc.status_da(g, dia)
        if garantia_svc.entregue_sem_data(g.data_inicio, situacoes.get(g.pedido_bling)):
            status = STATUS_ENTREGUE_SEM_DATA
        saida.append(
            {
                "id": g.id,
                "de_onde": escolhidas[g.id],
                "status": status,
                "status_rotulo": STATUS_GARANTIA_IA[status],
                "inicio": _iso(g.data_inicio),
                "inicio_origem": (
                    _ORIGEM_DA_DATA_IA.get(g.entrega_origem or "")
                    or garantia_svc.ORIGEM_ROTULOS.get(g.entrega_origem or "")
                    if g.data_inicio
                    else None
                ),
                "fim_hardware": _iso(g.fim_hardware),
                "fim_software": _iso(g.fim_software),
                "produto": _produto(g),
            }
        )
    return {"consultada": True, "garantias": saida}


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
    # O comprador pediu o cancelamento na plataforma? (o motivo do 83955)
    status_plataforma = ag_cancelamento.status_na_plataforma(conversa.dados)

    for chave in chaves:
        pedido = await _seguro(
            session,
            "pedido",
            lambda chave=chave: _pedido(session, chave, status_plataforma=status_plataforma),
            None,
            cid,
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
