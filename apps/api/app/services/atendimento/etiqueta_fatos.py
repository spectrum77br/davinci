"""Os FATOS que decidem a etiqueta de uma conversa — e o elo conversa ↔ pedido.

`etiqueta.calcular` é PURA (recebe `FatosEtiqueta`, devolve a etiqueta);
quem vai ao banco buscar os fatos é este módulo. Separado de propósito: a
regra de prioridade se testa sem banco, e quem completar uma fonte nova
(frentes A e B, item 4 do outro dev) mexe só aqui.

COMO LIGAR CONVERSA → PEDIDO DO BLING (o mesmo caminho do painel do pedido,
`contexto.py`, e da Central de chamados, `chamados.lookup_pedido`):

  1. As CHAVES da conversa (`contexto._chaves_do_pedido`), na ordem:
     `conversa.pedido_marketplace` (o número do pedido NA PLATAFORMA — Shopee
     `order_sn`, ML pack/`order_id`, TikTok `order_id`, Amazon
     `AmazonOrderId`) e, no ML, `dados.pack_id`, `dados.order_id` e o order
     do retrato (`dados.pedido_mkt.pedido`): no pedido de carrinho o Bling
     grava ora o PACK, ora o ORDER em `numeroloja`, e a conversa do pack
     guarda o PACK em `pedido_marketplace` (em produção o pack nunca traz
     `order_id` à vista: 53 das 147 conversas do pós-venda são carrinho).
  2. O espelho `bling_orders` tem UMA LINHA POR ITEM: `numero` = nº do Bling,
     `numeroloja` = nº na plataforma, `situacao` = id da situação (TEXTO),
     `status` = o PINO da Margem ('Pendente'/'Reprovado'/'Aprovado'),
     `loja` = id da loja no Bling (→ `store_info.bling_store_id`). Casa por
     `numero` OU `numeroloja`; com mais de um pedido para a mesma chave
     (raro), vale o mais recente (`data` desc). As chaves se tentam EM ORDEM:
     a primeira que achar é o pedido.
  3. A partir do nº do Bling: Logística (`logistica.pedido_bling` ou
     `.pedido_marketplace`; `meli_status` com `return_*`/`claim_*` do ML),
     `devolucao_rastreio.pedido_bling`, `devolutions`, `chamados`
     (`pedido_bling`/`pedido_marketplace`) e `nf_nota.pedido_bling`.
  4. Reclamações da plataforma (`atendimento_reclamacoes`): pela
     `conversa_id` que o leitor gravou OU por (plataforma, `pedido_marketplace`
     ou `dados.pack_id` da reclamação ∈ chaves — `condicao_do_pedido`): a
     reclamação do ML é gravada pelo ORDER, e no carrinho com vários pedidos
     a conversa do pack só tem o PACK. Assim a conversa do pack e a conversa
     `reclamacao` do mesmo pedido ganham a mesma etiqueta (e o mesmo cartão).

  5. Avaliações de venda PENDENTES (`atendimento_avaliacoes_loja.
     pendente_desde`, 02/10/2026): do mesmo jeito que a reclamação — pela
     `conversa_id` (a conversa `avaliacao` criada para ela) OU por
     (plataforma, `pedido` ou `dados.pack_id` da avaliação ∈ chaves —
     `condicao_avaliacao_do_pedido`). A opinião do ML vem pelo ORDER; a
     conversa do pack de carrinho só tem o pack.

  6. Carrinho abandonado do site (`atendimento_carrinhos`, 02/10/2026): só
     pela `conversa_id` (a conversa `carrinho` do lojista; o site não tem
     pedido no Bling). O episódio MAIS RECENTE da conversa decide: aberto →
     CARRINHO; recuperado → a base vira pós-venda ("virou pedido");
     não recuperado / resolvido → pré-venda.
  7. Mídia (02/10/2026): a conversa `comentario` (comentário ou menção nas
     redes) tem a base MÍDIA — nenhuma consulta: é o canal que diz.

  Produção, 01/10/2026: pedido ligado em 2.373 de 3.477 conversas Shopee, 244
  de 515 TikTok e 146 de 146 do pós-venda do ML; a pergunta do ML nunca tem
  pedido. 86 conversas ligadas a pedido em 83957 e 5 a pedido em 83955.

  O CAMINHO INVERSO (pedido → conversa) é `conversas_do_pedido` /
  `conversa_do_pedido` (pelo nº na plataforma) e `conversas_do_pedido_bling`
  (pelo nº do Bling): o gancho do webhook do Bling e o cartão de Ag.
  cancelamento (item 4) chegam à conversa que já existe por aqui.

O que JÁ é fonte (01/10/2026):
  • pré × pós-venda: a regra dos filtros da lista (`e_pos_venda`);
  • Ag. cancelamento: Bling em 83955 (`ag_cancelamento_visivel`), com a
    trilha da Margem para separar a trava do robô e, desde 02/10/2026 (item
    4), o MOTIVO (`ag_cancelamento.classificar`): os fatos da NF
    (`nf_faturamento`, `nf_nota`, `nf_etiqueta_arquivo`) e a trilha mais
    nova da `margem_audit` são lidos só para os pedidos em 83955
    (`_fatos_nf`);
  • Devolução: Bling em 83957 + `atendimento_reclamacoes` tipo `devolucao`;
  • Reclamação: `atendimento_reclamacoes` tipo `reclamacao`/`mediacao` +
    a reserva do pack do ML (`_claims_do_pack`): `dados.claim_ids` SÓ com o
    chat bloqueado pela reclamação (`substatus_ml` em
    `SUBSTATUS_ML_RECLAMACAO`) e só os ids que a tabela AINDA NÃO conhece.
    O ML NÃO esvazia `claim_ids` quando a reclamação acaba (produção,
    01/10/2026: dos 10 packs com `claim_ids`, 6 `active` e 2
    `blocked_by_cancelled_order` eram de reclamação encerrada ou de
    cancelamento) — `claim_ids` sozinho pintaria o pack de vermelho para
    sempre. O que a busca de reclamações já leu vale pela tabela (a
    devolução do ML, `returns`, também vem em `claim_ids` e é Devolução; a
    encerrada na tabela não reabre por um `claim_ids` velho do pack);
  • reclamação/devolução encerrada (`encerrada_em` preenchido, ou o Bling
    saiu de 83957): o fato some e a etiqueta volta à base (pós-venda);
  • Avaliação (02/10/2026): avaliação de venda PENDENTE — sem resposta da
    loja depois da carência (Shopee) ou nota 1–3 sem tratar (ML). Quem
    decide a pendência é `services/atendimento/avaliacoes.py`; aqui só se
    lê `pendente_desde`. Respondida ou tratada, o fato some e a etiqueta
    volta ao que os outros fatos dão.
A LOGÍSTICA ENTRA PELA TABELA: `reclamacoes.sincronizar_reclamacoes_ml` e
  `reclamacoes_devolucoes.ligar_devolucoes` (frente A) gravam em
  `atendimento_reclamacoes` a devolução e a disputa de Shopee/TikTok que a
  Logística já lê (`meli_status.return_status`, `devolucao_rastreio`, com a
  regra "vivo × encerrado" de `logistica_rules.RETURN_ENCERRADO`) e os
  claims do ML. Nada de consulta nova à Logística neste módulo: uma fonte
  só para a etiqueta e para o cartão da reclamação — duas leituras com
  regras de "encerrado" diferentes dariam etiqueta e cartão discordando.
ITEM 4 (02/10/2026): a classificação do motivo do 83955 mora em
  `ag_cancelamento.py` (puro); `ag_cancelamento_visivel` só delega a ela, e
  o motivo da etiqueta passa a dizer o porquê (`_montar_fatos`). A lista Ag.
  cancelamento (fase 4b, 05/10/2026) parte do PEDIDO: `pedidos_em_83955`
  monta os pedidos em 83955 com as mesmas funções, e a conversa (quando
  houver) vem de `conversas_do_pedido_bling`.

DUAS PORTAS, A MESMA REGRA: `fatos_da_conversa` (uma conversa — o gancho do
sync, a troca à mão) e `fatos_em_lote` (o cron e o preenchimento, centenas
por vez em 4 ou 5 consultas). As duas montam os fatos com as mesmas funções
puras (`_pedido_das_linhas`, `_montar_fatos`); a de uma conversa é o lote
de um.

Nada aqui escreve no banco, e nada sai para a plataforma ou para o Bling.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoAvaliacaoLoja,
    AtendimentoCarrinho,
    AtendimentoConversa,
    AtendimentoReclamacao,
    BlingOrder,
    MargemAudit,
    NfEtiquetaArquivo,
    NfFaturamento,
    NfNota,
    StoreInfo,
)
from app.services.atendimento.ag_cancelamento import (
    MARCAS_DA_NF,
    ORIGEM_ROBO_MARGEM,  # noqa: F401 — reexportada
    PINO_MARGEM_PENDENTE,  # noqa: F401 — reexportada
    PINO_MARGEM_REPROVADO,  # noqa: F401 — reexportada
    classificar,
    cortar,
    status_na_plataforma,
)
from app.services.atendimento.constantes import (
    CANAIS_SEMPRE_POS_VENDA,
    CANAL_AVALIACAO,
    CANAL_CARRINHO,
    CANAL_COMENTARIO,
    CANAL_PERGUNTA,
    CANAL_RECLAMACAO,
    CARRINHO_ABERTO,
    CARRINHO_RECUPERADO,
    NOME_SITE,
    PUBLICACAO_MENCAO,
    RECLAMACAO_TIPO_DEVOLUCAO,
    TIPOS_QUE_SAO_RECLAMACAO,
    reclamacao_aberta,
)
from app.services.atendimento.contexto import _chaves_do_pedido
from app.services.atendimento.etiqueta import FatosEtiqueta
from app.services.bling_situacoes import (
    SITUACAO_AGUARDANDO_CANCELAMENTO_STR,
    SITUACAO_AGUARDANDO_DEVOLUCAO_STR,
)

logger = structlog.get_logger()

# `ORIGEM_ROBO_MARGEM` (a trilha do robô da Margem), `PINO_MARGEM_PENDENTE`
# e `PINO_MARGEM_REPROVADO` moram em `ag_cancelamento.py` desde 02/10/2026 e
# continuam importáveis daqui (reexportadas acima).

# Como o atendimento chama a plataforma (`atendimento_conversas.plataforma`)
# a partir do que outros cadastros gravam (`store_info.platform`, o enum das
# integrações). O que não estiver aqui passa como veio, em minúsculas.
_APELIDOS_PLATAFORMA = {
    "mercadolivre": "ml",
    "mercado_livre": "ml",
    "mercado livre": "ml",
    "meli": "ml",
}

# O `substatus_ml` do pack quando o ML fechou o chat POR CAUSA da reclamação
# (`conversation_status.substatus`). É a única prova, no pack, de que a
# reclamação está aberta: o ML deixa os ids em `claim_ids` depois de ela
# acabar (ver o topo). O resto (`active`, `blocked_by_cancelled_order`,
# `blocked_by_time`…) não liga a reserva — a reclamação aberta de verdade
# chega pela tabela (a busca do ML lê TODAS as abertas da conta).
SUBSTATUS_ML_RECLAMACAO = frozenset({"blocked_by_claim", "blocked_by_mediation"})

# Nome da plataforma no motivo ("aberta no ML").
_NOME_PLATAFORMA = {
    "ml": "ML",
    "shopee": "Shopee",
    "tiktok": "TikTok",
    "amazon": "Amazon",
    "magalu": "Magalu",
    "temu": "Temu",
    "aliexpress": "AliExpress",
    "instagram": "Instagram",
    "facebook": "Facebook",
}
_NOME_TIPO = {
    "reclamacao": "Reclamação",
    "mediacao": "Mediação",
    "devolucao": "Devolução",
}


@dataclass(frozen=True)
class PedidoBling:
    """O pedido do Bling de uma conversa, no grão de PEDIDO (não de item).

    É o que `ag_cancelamento_visivel` recebe: tudo o que a regra precisa já
    vem carregado, para ela ficar pura (o item 4 troca a regra sem banco).
    """

    numero: str
    numeroloja: str | None = None
    bling_id: int | None = None
    # Id da situação, em TEXTO (como em `bling_orders.situacao`).
    situacao: str | None = None
    # `bling_orders.status`: o pino da Margem ('Pendente'/'Reprovado'/'Aprovado').
    pino_margem: str | None = None
    # Quem pôs o pedido em 83955, pela trilha da Margem (`margem_audit`): a
    # `origem` da ÚLTIMA mudança de situação registrada pelo DaVinci, SE ela
    # foi a entrada em 83955 ('margens_auto' = robô da Margem; 'margens' =
    # pessoa na aba Margem). None = a entrada não está na trilha: o sweep de
    # NF (falta de estoque, restrição de envio) e o movimento à mão no Bling
    # não gravam lá — e, se a última mudança registrada foi uma SAÍDA de
    # 83955 (o resgate do robô: 83955 → Em aberto), quem pôs o pedido de
    # volta em 83955 depois disso também não foi a Margem.
    origem_ag_cancelamento: str | None = None
    # `bling_orders.loja` (→ `store_info.bling_store_id`).
    loja: str | None = None
    # O pino foi gravado por PESSOA na aba Margem (`bling_orders.aprovado_por`
    # preenchido). O Reprovar da pessoa num pedido que JÁ está fora de "Em
    # aberto" (o robô segurou em 83955) não muda a situação nem grava trilha
    # de situação (`routers/margens._apply_bling_decision_by_pedido`): sem
    # isto, a última trilha continuaria sendo a do robô e a venda que vai ser
    # cancelada ficaria escondida.
    pino_por_pessoa: bool = False
    # Os SKUs dos itens do pedido no espelho (`bling_orders.item_codigo`),
    # na ordem dos itens: a marca de falta de estoque só vale com algum SKU
    # do erro ainda no pedido (`ag_cancelamento.nf_vencida`).
    skus_itens: tuple[str, ...] = ()
    # ── Os fatos da NF: lidos SÓ para o pedido em 83955 (`_fatos_nf`) ──
    # `nf_faturamento.status_faturamento` ('sem_estoque'/'restricao' = o
    # sweep de NF pôs em 83955; 'ok'/'processando' = a NF já saiu).
    nf_status: str | None = None
    # `nf_faturamento.erro_faturamento` ("… — saldo negativo: <skus>").
    nf_erro: str | None = None
    # Quando a marca foi gravada (`nf_faturamento.updated_at`).
    nf_marcada_em: datetime | None = None
    # A trilha mais nova da `margem_audit` de mudança de situação (qualquer
    # direção, qualquer origem) ou de troca de SKU — o pedido mexeu depois
    # da marca? Só lida quando há marca do sweep ('sem_estoque'/'restricao').
    ultima_trilha_em: datetime | None = None
    # O pedido tem NF (`nf_nota.pedido_bling`) ou etiqueta
    # (`nf_etiqueta_arquivo.pedido_bling`, ou `nf_faturamento.status_etiqueta`
    # preenchido): o 83955 veio depois, à mão.
    tem_nf_ou_etiqueta: bool = False


def normalizar_plataforma(plataforma: str | None) -> str | None:
    """'mercadolivre' → 'ml'; vazio → None (vale qualquer plataforma)."""
    v = (plataforma or "").strip().lower()
    if not v:
        return None
    return _APELIDOS_PLATAFORMA.get(v, v)


def chaves_do_pedido(conversa: AtendimentoConversa) -> list[str]:
    """Os números pelos quais o pedido da conversa pode estar no espelho do Bling."""
    return _chaves_do_pedido(conversa)


def e_pos_venda(canal: str | None, pedido_marketplace: str | None) -> bool:
    """A conversa é DEPOIS da compra? A regra dos filtros Pré-venda/Pós-venda.

    Pós-venda do ML, SAC da Magalu, e-mail da Amazon e a reclamação são
    sempre depois da compra; a pergunta no anúncio, sempre antes; o chat
    (Shopee, TikTok, Magalu, Temu, AliExpress), pelo pedido ligado.
    """
    if canal in CANAIS_SEMPRE_POS_VENDA:
        return True
    if canal == CANAL_PERGUNTA:
        return False
    return bool((pedido_marketplace or "").strip())


def ag_cancelamento_visivel(pedido: PedidoBling | None) -> bool:
    """O pedido em "Aguardando Cancelamento" (83955) vira etiqueta Ag. cancelamento?

    O ENCAIXE DO ITEM 4 (02/10/2026): a regra é a do classificador do motivo
    (`ag_cancelamento.classificar`, `Motivo.etiqueta`); aqui só se delega.
    A tabela-verdade de antes continua a mesma (os 8 casos). DUAS entradas
    mudaram de visibilidade: (1) trilha `margens_auto` + pino 'Aprovado' +
    marca viva de falta de estoque/restrição — a pessoa já aprovou a margem
    e o que segura o pedido é a NF (passa a aparecer); (2) o pino 'Pendente'
    com trilha da PESSOA (`margens`) passa a ser trava da Margem (oculto),
    como o 'Pendente' sem trilha (crítica A2). E a reprovação da Margem sem
    pessoa registrada continua aparecendo, mas sem falar em cancelamento
    (`Motivo.fala_cancelamento`).

    HISTÓRICO — a regra de 01/10/2026 (a base do item 4; o que mudou está
    acima): sim, MENOS quando quem pôs o pedido lá foi o
    robô da Margem — a trava interna de margem (264 pedidos em 30 dias, 182
    voltaram para Em aberto; levantamento §2.3). Abrir cartão ou avisar o
    comprador nesses casos falaria de cancelamento a quem só está com o
    pedido em análise. Robô = a última mudança de situação na trilha da
    Margem é a entrada em 83955 com origem `margens_auto` (ver
    `PedidoBling.origem_ag_cancelamento`), ou, sem essa trilha, o pino
    'Pendente' (segurado para análise) está gravado. A reprovação feita por
    PESSOA na aba Margem (origem `margens`, ou o pino 'Reprovado' gravado
    por pessoa — `PedidoBling.pino_por_pessoa` — mesmo num pedido que o
    robô segurou) aparece (desde o item 4, só o 'Reprovado' com pessoa
    registrada fala em cancelamento). Falta de estoque e
    restrição de envio (sweep de NF) e o
    movimento à mão no Bling não gravam trilha: aparecem — inclusive o
    pedido que o robô segurou, resgatou (83955 → Em aberto, pino
    'Aprovado') e que depois voltou a 83955 por outro motivo.
    """
    motivo = classificar(pedido)
    return bool(motivo and motivo.etiqueta)


# ── Pedido do Bling ───────────────────────────────────────────────────────

# As colunas do espelho que a etiqueta lê (uma linha por ITEM do pedido).
_COLUNAS_BLING = (
    BlingOrder.numero,
    BlingOrder.numeroloja,
    BlingOrder.bling_id,
    BlingOrder.situacao,
    BlingOrder.status,
    BlingOrder.loja,
    BlingOrder.data,
    BlingOrder.item_index,
    BlingOrder.aprovado_por,
    # O SKU do item: a marca de falta de estoque vale só com o SKU do erro
    # ainda no pedido (item 4).
    BlingOrder.item_codigo,
)
# Teto de chaves por consulta ao espelho (o lote do cron manda centenas).
_CHAVES_POR_CONSULTA = 1000


def _recentes_primeiro(linha: Any) -> tuple:
    """Ordem do espelho: o pedido mais recente (`data` desc, sem data no fim), item 0 antes."""
    data: datetime | None = linha.data
    return (data is None, -data.timestamp() if data is not None else 0.0, linha.item_index or 0)


def _pedido_das_linhas(chaves: Sequence[str], linhas: Sequence[Any]) -> PedidoBling | None:
    """O pedido das CHAVES (em ordem) entre as linhas do espelho. PURA.

    A primeira chave que achar vale (como o painel); com mais de um pedido
    para a mesma chave, o mais recente. Sem a origem do 83955 — essa vem da
    trilha da Margem (`_origens_ag_cancelamento`), lida depois e só para
    quem está em 83955.
    """
    for chave in chaves:
        casam = sorted(
            (r for r in linhas if chave in (r.numero, r.numeroloja)), key=_recentes_primeiro
        )
        if not casam or not casam[0].numero:
            continue
        primeira = casam[0]
        # Só as linhas-item do pedido escolhido; o pino é gravado em todas
        # pela Margem, mas basta uma preenchida.
        do_pedido = [r for r in casam if r.numero == primeira.numero]
        com_pino = next((r for r in do_pedido if r.status), None)
        skus = (str(getattr(r, "item_codigo", None) or "").strip() for r in do_pedido)
        return PedidoBling(
            numero=primeira.numero,
            numeroloja=primeira.numeroloja,
            bling_id=primeira.bling_id,
            situacao=(primeira.situacao or "").strip() or None,
            pino_margem=com_pino.status if com_pino is not None else None,
            loja=primeira.loja,
            pino_por_pessoa=bool(
                com_pino is not None and getattr(com_pino, "aprovado_por", None) is not None
            ),
            skus_itens=tuple(dict.fromkeys(s for s in skus if s)),
        )
    return None


async def _linhas_bling(session: AsyncSession, chaves: Iterable[str]) -> list[Any]:
    """As linhas do espelho cujo nº do Bling OU nº na plataforma está nas chaves."""
    lista = sorted({str(c).strip() for c in chaves if str(c or "").strip()})
    linhas: list[Any] = []
    for i in range(0, len(lista), _CHAVES_POR_CONSULTA):
        parte = lista[i : i + _CHAVES_POR_CONSULTA]
        linhas += (
            await session.execute(
                select(*_COLUNAS_BLING).where(
                    or_(BlingOrder.numero.in_(parte), BlingOrder.numeroloja.in_(parte))
                )
            )
        ).all()
    return linhas


async def _origens_ag_cancelamento(
    session: AsyncSession, numeros: Iterable[str]
) -> dict[str, str | None]:
    """{nº do Bling: quem o pôs em 83955} pela trilha da Margem (ver `PedidoBling`).

    A ÚLTIMA mudança de situação registrada de cada pedido: se foi a entrada
    em 83955, vale a origem dela; se foi outra (a saída, no resgate), None —
    a entrada de agora não passou pela trilha.
    """
    lista = sorted({n for n in numeros if n})
    if not lista:
        return {}
    linhas = (
        await session.execute(
            select(MargemAudit.pedido_bling, MargemAudit.origem, MargemAudit.valor_novo)
            .where(MargemAudit.pedido_bling.in_(lista), MargemAudit.acao == "situacao")
            .distinct(MargemAudit.pedido_bling)
            .order_by(MargemAudit.pedido_bling, MargemAudit.created_at.desc())
        )
    ).all()
    return {
        r.pedido_bling: (
            r.origem
            if (r.valor_novo or "").strip() == SITUACAO_AGUARDANDO_CANCELAMENTO_STR
            else None
        )
        for r in linhas
    }


async def _fatos_nf(session: AsyncSession, numeros: Iterable[str]) -> dict[str, dict[str, Any]]:
    """{nº do Bling: campos da NF do `PedidoBling`} — o que o motivo do 83955 precisa.

    Em lote, 2 ou 3 consultas: `nf_faturamento` (a marca do sweep, o erro,
    a hora da marca e o status da etiqueta); `nf_nota` ∪ `nf_etiqueta_arquivo`
    (o pedido já tem NF ou etiqueta); e, SÓ para quem tem a marca do sweep
    ('sem_estoque'/'restricao'), a trilha mais nova da `margem_audit` de
    situação (qualquer direção) ou de SKU — a marca é velha se algo mexeu
    no pedido depois dela (`ag_cancelamento.nf_vencida`). Quem chama passa
    só os pedidos em 83955.

    LIMITES CONHECIDOS (4a): a hora da marca é o `updated_at` da linha, que
    anda com QUALQUER update dela (etiqueta, impressão, `nf_recuperar`) — uma
    marca velha pode parecer mais nova que a trilha que a envelheceu (na
    falta de estoque o SKU do erro ainda segura; na restrição, só a trilha).
    E qualquer `status_etiqueta` preenchido conta como etiqueta, inclusive
    'erro'/'processando' de um ciclo anterior: a marca viva vira
    `pos_nf_manual` (o lado seguro, sem falar em cancelamento).
    """
    lista = sorted({n for n in numeros if n})
    if not lista:
        return {}
    marcas = {
        r.pedido_bling: r
        for r in (
            await session.execute(
                select(
                    NfFaturamento.pedido_bling,
                    NfFaturamento.status_faturamento,
                    NfFaturamento.erro_faturamento,
                    NfFaturamento.status_etiqueta,
                    NfFaturamento.updated_at,
                ).where(NfFaturamento.pedido_bling.in_(lista))
            )
        ).all()
    }
    com_nf_ou_etiqueta = set(
        (
            await session.execute(
                select(NfNota.pedido_bling)
                .where(NfNota.pedido_bling.in_(lista))
                .union(
                    select(NfEtiquetaArquivo.pedido_bling).where(
                        NfEtiquetaArquivo.pedido_bling.in_(lista)
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    marcados = sorted(
        n for n, r in marcas.items() if (r.status_faturamento or "").strip().lower() in MARCAS_DA_NF
    )
    trilhas: dict[str, datetime] = {}
    if marcados:
        trilhas = {
            r.pedido_bling: r.em
            for r in (
                await session.execute(
                    select(MargemAudit.pedido_bling, func.max(MargemAudit.created_at).label("em"))
                    .where(
                        MargemAudit.pedido_bling.in_(marcados),
                        MargemAudit.acao.in_(("situacao", "sku")),
                    )
                    .group_by(MargemAudit.pedido_bling)
                )
            ).all()
        }
    fatos: dict[str, dict[str, Any]] = {}
    for numero in lista:
        tem = numero in com_nf_ou_etiqueta
        marca = marcas.get(numero)
        if marca is None:
            fatos[numero] = {"tem_nf_ou_etiqueta": tem}
            continue
        fatos[numero] = {
            "nf_status": (marca.status_faturamento or "").strip() or None,
            "nf_erro": marca.erro_faturamento,
            "nf_marcada_em": marca.updated_at,
            "ultima_trilha_em": trilhas.get(numero),
            "tem_nf_ou_etiqueta": tem or bool((marca.status_etiqueta or "").strip()),
        }
    return fatos


async def _com_origem(
    session: AsyncSession, pedidos: Iterable[PedidoBling | None]
) -> dict[str, PedidoBling]:
    """{nº do Bling: pedido} com a origem do 83955 e os fatos da NF preenchidos.

    Só os pedidos em 83955 consultam (a trilha da Margem e `_fatos_nf`): um
    lote sem pedido em 83955 não faz consulta nenhuma aqui.

    Os fatos da NF têm SAVEPOINT e `try` PRÓPRIOS: se a leitura falhar, os
    campos da NF ficam no padrão e o resto segue — o painel não perde o
    pedido inteiro (`_seguro`) e o cron não perde o lote. É o lado seguro:
    sem marca viva da NF, a trava da Margem (regras 1 a 4) é a mesma de
    antes do item 4 e o resto cai em "movido à mão", sem falar em
    cancelamento.
    """
    por_numero = {p.numero: p for p in pedidos if p is not None}
    em_83955 = [
        n for n, p in por_numero.items() if p.situacao == SITUACAO_AGUARDANDO_CANCELAMENTO_STR
    ]
    if not em_83955:
        return por_numero
    origens = await _origens_ag_cancelamento(session, em_83955)
    nf: dict[str, dict[str, Any]] = {}
    try:
        async with session.begin_nested():
            nf = await _fatos_nf(session, em_83955)
    except Exception as e:  # noqa: BLE001 — os fatos da NF nunca derrubam o pedido
        logger.warning("atendimento_fatos_nf_falhou", pedidos=len(em_83955), err=type(e).__name__)
        nf = {}
    for numero in em_83955:
        por_numero[numero] = replace(
            por_numero[numero], origem_ag_cancelamento=origens.get(numero), **nf.get(numero, {})
        )
    return por_numero


async def pedido_bling(session: AsyncSession, chaves: str | Iterable[str]) -> PedidoBling | None:
    """O pedido do Bling por nº do Bling OU nº na plataforma; chaves em ordem.

    A primeira chave que achar vale (como o painel). Lê a trilha da Margem
    e os fatos da NF só quando o pedido está em 83955 — é a única regra que
    precisa deles.
    """
    lista = [chaves] if isinstance(chaves, str) else list(chaves)
    lista = [str(c).strip() for c in lista if str(c or "").strip()]
    if not lista:
        return None
    pedido = _pedido_das_linhas(lista, await _linhas_bling(session, lista))
    if pedido is None:
        return None
    return (await _com_origem(session, [pedido]))[pedido.numero]


async def pedido_bling_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa
) -> PedidoBling | None:
    chaves = chaves_do_pedido(conversa)
    if not chaves:
        return None
    return await pedido_bling(session, chaves)


# Teto de pedidos da lista Ag. cancelamento (em produção são 10 a 20).
MAX_PEDIDOS_83955 = 300


async def pedidos_em_83955(session: AsyncSession, desde: datetime) -> list[PedidoBling]:
    """Os pedidos em 83955 (Aguardando Cancelamento) desde `desde`, o mais recente antes.

    A lista Ag. cancelamento do item 4 (fase 4b): pelo PEDIDO, não pela
    conversa — só 27 dos 78 pedidos sem estoque tinham conversa no DaVinci.
    As mesmas funções da etiqueta (`_pedido_das_linhas`, `_com_origem` com a
    trilha da Margem e os fatos da NF): o motivo sai igual ao do painel.
    """
    linhas = (
        await session.execute(
            select(*_COLUNAS_BLING).where(
                BlingOrder.situacao == SITUACAO_AGUARDANDO_CANCELAMENTO_STR,
                BlingOrder.data >= desde,
            )
        )
    ).all()
    por_numero: dict[str, list[Any]] = {}
    for r in sorted(linhas, key=_recentes_primeiro):
        if r.numero:
            por_numero.setdefault(r.numero, []).append(r)
    numeros = list(por_numero)[:MAX_PEDIDOS_83955]
    pedidos = [_pedido_das_linhas([n], por_numero[n]) for n in numeros]
    com_origem = await _com_origem(session, pedidos)
    return [com_origem[n] for n in numeros if n in com_origem]


# ── Reclamações da plataforma ─────────────────────────────────────────────


def _mais_urgente_primeiro(r: AtendimentoReclamacao) -> tuple:
    """Prazo mais curto primeiro (sem prazo no fim); empate: a aberta mais recente."""
    prazo, aberta = r.prazo_em, r.aberta_em
    return (
        prazo is None,
        prazo.timestamp() if prazo is not None else 0.0,
        aberta is None,
        -aberta.timestamp() if aberta is not None else 0.0,
    )


def pedidos_da_reclamacao(r: AtendimentoReclamacao) -> set[str]:
    """Os números pelos quais a reclamação casa com um pedido: o dela e, no ML, o pack.

    A reclamação do ML é gravada pelo ORDER (`resource_id`); a conversa do
    pack de carrinho guarda o PACK. O leitor grava o pack em `dados.pack_id`.
    """
    numeros = {str(r.pedido_marketplace).strip()} if r.pedido_marketplace else set()
    dados = r.dados if isinstance(r.dados, dict) else {}
    pack = str(dados.get("pack_id") or "").strip()
    if pack:
        numeros.add(pack)
    numeros.discard("")
    return numeros


def condicao_do_pedido(chaves: Sequence[str]):
    """SQL de `pedidos_da_reclamacao`: o pedido da reclamação OU o pack dela está nas chaves."""
    return or_(
        AtendimentoReclamacao.pedido_marketplace.in_(list(chaves)),
        AtendimentoReclamacao.dados["pack_id"].astext.in_(list(chaves)),
    )


def _da_conversa(
    r: AtendimentoReclamacao, conversa: AtendimentoConversa, chaves: Sequence[str]
) -> bool:
    """A reclamação é desta conversa? Pela `conversa_id` OU pelo pedido, na mesma plataforma."""
    if r.conversa_id is not None and r.conversa_id == conversa.id:
        return True
    return r.plataforma == conversa.plataforma and bool(pedidos_da_reclamacao(r) & set(chaves))


async def _reclamacoes_abertas_de(
    session: AsyncSession, ids: Sequence[UUID], chaves: Iterable[str]
) -> list[AtendimentoReclamacao]:
    """As ABERTAS (`encerrada_em IS NULL`) ligadas a estas conversas ou a estes pedidos."""
    lista = sorted({c for c in chaves if c})
    conds = [AtendimentoReclamacao.conversa_id.in_(list(ids))] if ids else []
    if lista:
        conds.append(condicao_do_pedido(lista))
    if not conds:
        return []
    return list(
        (
            await session.execute(
                select(AtendimentoReclamacao).where(
                    AtendimentoReclamacao.encerrada_em.is_(None), or_(*conds)
                )
            )
        )
        .scalars()
        .all()
    )


async def reclamacoes_abertas(
    session: AsyncSession, conversa: AtendimentoConversa, chaves: list[str] | None = None
) -> list[AtendimentoReclamacao]:
    """Reclamações/mediações/devoluções ABERTAS (`encerrada_em IS NULL`) da conversa.

    Pela `conversa_id` que o leitor gravou OU pelo pedido (mesma plataforma).
    Prazo mais curto primeiro (sem prazo no fim).
    """
    if chaves is None:
        chaves = chaves_do_pedido(conversa)
    conds = [AtendimentoReclamacao.conversa_id == conversa.id]
    if chaves:
        conds.append(
            and_(
                AtendimentoReclamacao.plataforma == conversa.plataforma,
                condicao_do_pedido(chaves),
            )
        )
    return list(
        (
            await session.execute(
                select(AtendimentoReclamacao)
                .where(AtendimentoReclamacao.encerrada_em.is_(None), or_(*conds))
                .order_by(
                    AtendimentoReclamacao.prazo_em.asc().nulls_last(),
                    AtendimentoReclamacao.aberta_em.desc().nulls_last(),
                )
            )
        )
        .scalars()
        .all()
    )


def _motivo_reclamacao(r: AtendimentoReclamacao) -> str:
    """Ex.: "Reclamação 5582543195 aberta no ML" — id e plataforma, sem texto do comprador."""
    plataforma = _NOME_PLATAFORMA.get(r.plataforma, r.plataforma)
    tipo = _NOME_TIPO.get(r.tipo, "Reclamação")
    return f"{tipo} {r.externo_id} aberta no {plataforma}"


# ── Avaliações de venda pendentes (RF8) ──────────────────────────────────

# "na Shopee", "no Mercado Livre" — o lugar, com a preposição certa.
_NA_PLATAFORMA = {"ml": "no Mercado Livre", "shopee": "na Shopee"}


def pedidos_da_avaliacao(a: AtendimentoAvaliacaoLoja) -> set[str]:
    """Os números pelos quais a avaliação casa com um pedido: o dela e, no ML, o pack.

    A opinião do ML vem com o ORDER (`order_id`); a conversa do pack de
    carrinho guarda o PACK. O leitor grava o pack em `dados.pack_id`.
    """
    numeros = {str(a.pedido).strip()} if a.pedido else set()
    dados = a.dados if isinstance(a.dados, dict) else {}
    pack = str(dados.get("pack_id") or "").strip()
    if pack:
        numeros.add(pack)
    numeros.discard("")
    return numeros


def condicao_avaliacao_do_pedido(chaves: Sequence[str]):
    """SQL de `pedidos_da_avaliacao`: o pedido da avaliação OU o pack dela está nas chaves."""
    return or_(
        AtendimentoAvaliacaoLoja.pedido.in_(list(chaves)),
        AtendimentoAvaliacaoLoja.dados["pack_id"].astext.in_(list(chaves)),
    )


def avaliacao_e_da_conversa(
    a: AtendimentoAvaliacaoLoja, conversa: AtendimentoConversa, chaves: Sequence[str]
) -> bool:
    """A avaliação é desta conversa? Pela `conversa_id` OU pelo pedido, na mesma plataforma."""
    if a.conversa_id is not None and a.conversa_id == conversa.id:
        return True
    return a.plataforma == conversa.plataforma and bool(pedidos_da_avaliacao(a) & set(chaves))


def pior_primeiro(a: AtendimentoAvaliacaoLoja) -> tuple:
    """A nota mais baixa primeiro; empate: a pendente há mais tempo."""
    desde = a.pendente_desde
    return (int(a.estrelas or 0), desde is None, desde.timestamp() if desde else 0.0)


async def avaliacoes_pendentes_de(
    session: AsyncSession, ids: Sequence[UUID], chaves: Iterable[str]
) -> list[AtendimentoAvaliacaoLoja]:
    """As PENDENTES (`pendente_desde IS NOT NULL`) ligadas a estas conversas ou pedidos."""
    lista = sorted({c for c in chaves if c})
    conds = [AtendimentoAvaliacaoLoja.conversa_id.in_(list(ids))] if ids else []
    if lista:
        conds.append(condicao_avaliacao_do_pedido(lista))
    if not conds:
        return []
    return list(
        (
            await session.execute(
                select(AtendimentoAvaliacaoLoja).where(
                    AtendimentoAvaliacaoLoja.pendente_desde.is_not(None), or_(*conds)
                )
            )
        )
        .scalars()
        .all()
    )


def pendentes_da_conversa(
    conversa: AtendimentoConversa,
    chaves: Sequence[str],
    pendentes: Iterable[AtendimentoAvaliacaoLoja],
) -> list[AtendimentoAvaliacaoLoja]:
    """Das pendentes lidas em lote, as desta conversa — a pior primeiro. PURA."""
    return sorted(
        (a for a in pendentes if avaliacao_e_da_conversa(a, conversa, chaves)), key=pior_primeiro
    )


def _motivo_avaliacao(a: AtendimentoAvaliacaoLoja) -> str:
    """Ex.: "Avaliação 2★ sem resposta da loja na Shopee" — nota e plataforma, sem o texto."""
    onde = _NA_PLATAFORMA.get(a.plataforma, f"no {a.plataforma}")
    if a.pode_responder:
        return f"Avaliação {a.estrelas}★ sem resposta da loja {onde}"
    return f"Avaliação {a.estrelas}★ {onde} ainda não tratada"


# ── Carrinho abandonado do site (RF9) ────────────────────────────────────


async def carrinhos_das_conversas(
    session: AsyncSession, ids: Sequence[UUID]
) -> list[AtendimentoCarrinho]:
    """Os carrinhos ligados a estas conversas, o MAIS RECENTE primeiro (por conversa)."""
    lista = sorted({i for i in ids if i is not None}, key=str)
    if not lista:
        return []
    return list(
        (
            await session.execute(
                select(AtendimentoCarrinho)
                .where(AtendimentoCarrinho.conversa_id.in_(lista))
                .order_by(
                    AtendimentoCarrinho.conversa_id,
                    AtendimentoCarrinho.detectado_em.desc(),
                    AtendimentoCarrinho.created_at.desc(),
                )
            )
        )
        .scalars()
        .all()
    )


def _fmt_dia(quando: datetime | None) -> str:
    """'30/09' no fuso de São Paulo (o motivo vai para a linha do tempo)."""
    if quando is None:
        return "—"
    from zoneinfo import ZoneInfo

    return quando.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m")


def _motivo_carrinho(c: AtendimentoCarrinho) -> str:
    """Ex.: "Carrinho parado desde 30/09 com 3 peças no site Charlots" — sem dado do lojista."""
    site = NOME_SITE.get(c.site, c.site)
    pecas = int(c.quantidade_total or 0)
    texto = f"{pecas} peça{'s' if pecas != 1 else ''}"
    return f"Carrinho parado desde {_fmt_dia(c.parado_desde)} com {texto} no site {site}"


# ── Fatos ─────────────────────────────────────────────────────────────────


def _claims_do_pack(conversa: AtendimentoConversa) -> list[str]:
    """Os `claim_ids` do pack do ML que ainda podem ser reclamação ABERTA.

    Só com o chat bloqueado pela reclamação (`substatus_ml` em
    `SUBSTATUS_ML_RECLAMACAO`): o ML deixa em `claim_ids` a reclamação já
    encerrada e a de cancelamento (ver o topo) — o pack `active` ou
    `blocked_by_cancelled_order` com `claim_ids` não é Reclamação.
    """
    if conversa.plataforma != "ml" or not reclamacao_aberta(conversa.dados):
        return []
    substatus = str(conversa.dados.get("substatus_ml") or "").strip().lower()
    if substatus not in SUBSTATUS_ML_RECLAMACAO:
        return []
    ids = conversa.dados.get("claim_ids")
    if not isinstance(ids, list):
        return []
    return [str(i).strip() for i in ids if str(i or "").strip()]


async def _claims_conhecidos(session: AsyncSession, ids: Iterable[str]) -> frozenset[str]:
    """Dos `claim_ids`, os que já são linha em `atendimento_reclamacoes` (aberta ou não)."""
    lista = sorted({i for i in ids if i})
    if not lista:
        return frozenset()
    return frozenset(
        (
            await session.execute(
                select(AtendimentoReclamacao.externo_id).where(
                    AtendimentoReclamacao.plataforma == "ml",
                    AtendimentoReclamacao.externo_id.in_(lista),
                )
            )
        )
        .scalars()
        .all()
    )


def _montar_fatos(
    conversa: AtendimentoConversa,
    pedido: PedidoBling | None,
    abertas: Sequence[AtendimentoReclamacao],
    conhecidos: frozenset[str] = frozenset(),
    avaliacoes: Sequence[AtendimentoAvaliacaoLoja] = (),
    carrinhos: Sequence[AtendimentoCarrinho] = (),
) -> FatosEtiqueta:
    """Os fatos de UMA conversa a partir do que já foi lido. PURA.

    `pedido` = o pedido do Bling dela (com a origem do 83955 e, em 83955,
    os fatos da NF que decidem o motivo — `_com_origem`); `abertas` =
    as reclamações abertas dela, a mais urgente primeiro; `conhecidos` =
    os `claim_ids` do pack que já são linha em `atendimento_reclamacoes` (a
    tabela decide o tipo e se está aberta — o pack só vale para o resto);
    `avaliacoes` = as avaliações PENDENTES dela, a pior primeiro;
    `carrinhos` = os carrinhos do site ligados a ela, o mais recente primeiro.
    """
    # Pré × pós-venda: a regra dos filtros da lista.
    tem_pedido = e_pos_venda(conversa.canal, conversa.pedido_marketplace)
    if not tem_pedido:
        motivo_pedido = "sem pedido ligado"
    elif (conversa.pedido_marketplace or "").strip():
        motivo_pedido = f"pedido {conversa.pedido_marketplace.strip()} ligado"
    else:
        motivo_pedido = "conversa depois da compra"

    # Carrinho do site: o episódio mais recente decide (aberto = CARRINHO;
    # recuperado = "virou pedido", a base vira pós-venda).
    ultimo_carrinho = carrinhos[0] if carrinhos else None
    carrinho_aberto = ultimo_carrinho is not None and ultimo_carrinho.situacao == CARRINHO_ABERTO
    if ultimo_carrinho is not None and ultimo_carrinho.situacao == CARRINHO_RECUPERADO:
        tem_pedido = True
        motivo_pedido = "carrinho recuperado: o lojista finalizou o pedido pelo WhatsApp"
    elif ultimo_carrinho is not None and not carrinho_aberto and not tem_pedido:
        motivo_pedido = "carrinho encerrado sem pedido"

    # Comentário/menção nas redes: a base é MÍDIA.
    midia = conversa.canal == CANAL_COMENTARIO
    motivo_midia = None
    if midia:
        rede = _NOME_PLATAFORMA.get(conversa.plataforma, conversa.plataforma)
        dados = conversa.dados if isinstance(conversa.dados, dict) else {}
        o_que = "Menção" if dados.get("tipo") == PUBLICACAO_MENCAO else "Comentário"
        motivo_midia = f"{o_que} no {rede}"

    # Bling: 83955 (Ag. cancelamento, filtrando a trava da Margem) e 83957.
    # O motivo diz o PORQUÊ (item 4): o classificador, com o status do
    # pedido na plataforma pelo retrato da conversa (o comprador pediu?).
    # Com a etiqueta visível o pedido está em 83955: o classificador sempre
    # devolve o motivo (o status da plataforma só escolhe entre regras visíveis).
    ag_cancelamento = ag_cancelamento_visivel(pedido)
    motivo_ag = None
    if ag_cancelamento and pedido is not None:
        m = classificar(pedido, status_plataforma=status_na_plataforma(conversa.dados))
        if m is not None:
            motivo_ag = cortar(
                f"pedido {pedido.numero} em Aguardando Cancelamento — {m.texto_interno}"
            )
    devolucao_bling = pedido is not None and pedido.situacao == SITUACAO_AGUARDANDO_DEVOLUCAO_STR

    # Reclamações da plataforma (a tabela) + o claim do pack do ML.
    reclamacoes = [r for r in abertas if r.tipo in TIPOS_QUE_SAO_RECLAMACAO]
    devolucoes = [r for r in abertas if r.tipo == RECLAMACAO_TIPO_DEVOLUCAO]

    motivo_reclamacao = None
    if reclamacoes:
        motivo_reclamacao = _motivo_reclamacao(reclamacoes[0])
    elif desconhecidos := [i for i in _claims_do_pack(conversa) if i not in conhecidos]:
        # A reserva do pack: chat bloqueado pela reclamação (o ML não
        # esvazia `claim_ids` quando ela acaba — `_claims_do_pack`). Só o id
        # que a busca de reclamações ainda não leu: o conhecido vale pela
        # tabela (tipo e encerramento).
        motivo_reclamacao = f"Reclamação {desconhecidos[0]} aberta no ML"
    # A conversa `reclamacao` cuja reclamação já não está aberta na tabela
    # (encerrada, ou ainda não lida) fica na base: pós-venda.

    motivo_devolucao = None
    if devolucoes:
        motivo_devolucao = _motivo_reclamacao(devolucoes[0])
    elif devolucao_bling and pedido is not None:
        motivo_devolucao = f"pedido {pedido.numero} em Aguardando Devolução no Bling"

    pior = avaliacoes[0] if avaliacoes else None
    return FatosEtiqueta(
        tem_pedido=tem_pedido,
        motivo_pedido=motivo_pedido,
        reclamacao_aberta=motivo_reclamacao is not None,
        motivo_reclamacao=motivo_reclamacao,
        devolucao_aberta=motivo_devolucao is not None,
        motivo_devolucao=motivo_devolucao,
        ag_cancelamento=ag_cancelamento,
        motivo_ag_cancelamento=motivo_ag,
        avaliacao_pendente=pior is not None,
        motivo_avaliacao=_motivo_avaliacao(pior) if pior is not None else None,
        estrelas_avaliacao=int(pior.estrelas) if pior is not None else None,
        carrinho_aberto=carrinho_aberto,
        motivo_carrinho=_motivo_carrinho(ultimo_carrinho) if carrinho_aberto else None,
        midia=midia,
        motivo_midia=motivo_midia,
        numero_bling=pedido.numero if pedido is not None else None,
    )


async def fatos_em_lote(
    session: AsyncSession, conversas: Sequence[AtendimentoConversa]
) -> dict[UUID, FatosEtiqueta]:
    """Os fatos de VÁRIAS conversas, lidos do banco em 4 a 9 consultas. Não escreve nada.

    O espelho do Bling (uma consulta para as chaves de todas), a trilha da
    Margem (só os pedidos em 83955) e, desde o item 4, os fatos da NF
    (também só em 83955: 2 a 3 consultas a mais, `_fatos_nf`; lote sem
    pedido em 83955 não faz nenhuma), as reclamações abertas e as
    avaliações pendentes (pela conversa ou pelo pedido). É o caminho do
    cron e do preenchimento: centenas de conversas sem uma consulta por
    conversa. Pode levantar (erro de banco): quem chama roda num SAVEPOINT.
    """
    if not conversas:
        return {}
    chaves = {c.id: chaves_do_pedido(c) for c in conversas}
    todas = {k for ks in chaves.values() for k in ks}
    linhas = await _linhas_bling(session, todas) if todas else []
    pedidos = {c.id: _pedido_das_linhas(chaves[c.id], linhas) for c in conversas}
    completos = await _com_origem(session, pedidos.values())
    abertas = await _reclamacoes_abertas_de(session, [c.id for c in conversas], todas)
    conhecidos = await _claims_conhecidos(
        session, (i for c in conversas for i in _claims_do_pack(c))
    )
    pendentes = await avaliacoes_pendentes_de(session, [c.id for c in conversas], todas)
    # Carrinhos do site: só as conversas `carrinho` (nenhuma consulta sem elas).
    carrinhos = await carrinhos_das_conversas(
        session, [c.id for c in conversas if c.canal == CANAL_CARRINHO]
    )
    fatos: dict[UUID, FatosEtiqueta] = {}
    for c in conversas:
        base = pedidos[c.id]
        pedido = completos.get(base.numero) if base is not None else None
        da_conversa = sorted(
            (r for r in abertas if _da_conversa(r, c, chaves[c.id])),
            key=_mais_urgente_primeiro,
        )
        avaliacoes = pendentes_da_conversa(c, chaves[c.id], pendentes)
        do_carrinho = [k for k in carrinhos if k.conversa_id == c.id]
        fatos[c.id] = _montar_fatos(c, pedido, da_conversa, conhecidos, avaliacoes, do_carrinho)
    return fatos


async def fatos_da_conversa(session: AsyncSession, conversa: AtendimentoConversa) -> FatosEtiqueta:
    """Tudo o que decide a etiqueta da conversa, lido do banco. Não escreve nada.

    O lote de um (`fatos_em_lote`): a mesma regra do cron. Pode levantar
    (erro de banco): quem chama (`etiqueta.recalcular_etiqueta`) roda num
    SAVEPOINT e, se der erro, deixa a etiqueta como está — um fato que não
    se conseguiu ler não pode derrubar a etiqueta para Pós-venda.
    """
    return (await fatos_em_lote(session, [conversa]))[conversa.id]


# ── Pedido → conversa ─────────────────────────────────────────────────────


async def conversas_do_pedido(
    session: AsyncSession, plataforma: str | None, pedido_marketplace: str | None
) -> list[AtendimentoConversa]:
    """As conversas de um pedido (nº NA PLATAFORMA), a principal primeiro.

    Casa `pedido_marketplace` e, no ML, também o `pack_id`/`order_id` de
    `dados` e o order do retrato (`dados.pedido_mkt.pedido`): o Bling grava
    ora o pack, ora o order em `numeroloja`, a reclamação do ML vem pelo
    order e a conversa do pack de carrinho guarda o pack.
    `plataforma` aceita o nome do cadastro ('mercadolivre' = 'ml'); None =
    qualquer uma. Ordem: as conversas com o comprador antes da conversa da
    reclamação, e dentro delas a de mensagem mais recente.
    """
    chave = str(pedido_marketplace or "").strip()
    if not chave:
        return []
    plat = normalizar_plataforma(plataforma)
    conds = [AtendimentoConversa.pedido_marketplace == chave]
    if plat in (None, "ml"):
        conds.append(AtendimentoConversa.dados["pack_id"].astext == chave)
        conds.append(AtendimentoConversa.dados["order_id"].astext == chave)
        conds.append(
            and_(
                AtendimentoConversa.plataforma == "ml",
                AtendimentoConversa.dados[("pedido_mkt", "pedido")].astext == chave,
            )
        )
    consulta = select(AtendimentoConversa).where(or_(*conds))
    if plat:
        consulta = consulta.where(AtendimentoConversa.plataforma == plat)
    consulta = consulta.order_by(
        # As conversas com o comprador primeiro; as que nasceram de uma
        # reclamação ou de uma avaliação, depois.
        AtendimentoConversa.canal.in_((CANAL_RECLAMACAO, CANAL_AVALIACAO)).asc(),
        AtendimentoConversa.ultima_mensagem_em.desc().nulls_last(),
        AtendimentoConversa.created_at.desc(),
    )
    return list((await session.execute(consulta)).scalars().all())


async def conversa_do_pedido(
    session: AsyncSession, plataforma: str | None, pedido_marketplace: str | None
) -> AtendimentoConversa | None:
    """A conversa principal de um pedido — onde vai o cartão (Ag. cancelamento, reclamação).

    None = o pedido não tem conversa (o item 4 cria a conversa interna).
    """
    conversas = await conversas_do_pedido(session, plataforma, pedido_marketplace)
    return conversas[0] if conversas else None


async def conversas_do_pedido_bling(
    session: AsyncSession, numero_bling: str | None
) -> list[AtendimentoConversa]:
    """As conversas de um pedido pelo nº do BLING (o gancho do webhook de situação).

    Acha o nº na plataforma (`numeroloja`) e a plataforma da loja
    (`store_info.bling_store_id` → `platform`) no espelho; loja sem cadastro
    = procura em todas as plataformas.
    """
    numero = str(numero_bling or "").strip()
    if not numero:
        return []
    linha = (
        await session.execute(
            select(BlingOrder.numeroloja, BlingOrder.loja)
            .where(BlingOrder.numero == numero, BlingOrder.numeroloja.is_not(None))
            .order_by(BlingOrder.data.desc().nulls_last())
            .limit(1)
        )
    ).first()
    if linha is None or not (linha.numeroloja or "").strip():
        return []
    plataforma = None
    if linha.loja:
        plataforma = await session.scalar(
            select(StoreInfo.platform).where(StoreInfo.bling_store_id == str(linha.loja)).limit(1)
        )
    return await conversas_do_pedido(session, plataforma, linha.numeroloja)
