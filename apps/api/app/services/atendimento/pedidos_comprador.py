"""As outras compras do comprador no painel do pedido (06/11/2026).

O dono, com o Duoke ao lado: "uma pessoa cancelou 2 pedidos, no nosso aparece o
código só do último e não o histórico inteiro". O chat da Shopee e do TikTok é UM
por comprador na loja e fica ligado ao último pedido; os outros dele não apareciam
no painel (a compradora de 17 pedidos com um deles em Aguardando Cancelamento).

Aqui só o BANCO: o índice `atendimento_pedidos_comprador` por (loja, comprador) e o
espelho do Bling por `numeroloja`. A foto, a variação, o valor pago e a logística
vêm depois, em lote e à parte (`enriquecer.retratos_da_loja`): o que está aqui
responde na hora e é o que o cartão mostra enquanto o resto não chega.

SÓ O CHAT DA SHOPEE E DO TIKTOK (`PLATAFORMAS_CHAT_DO_COMPRADOR`). No ML, a Amazon,
a Magalu e o e-mail a conversa é de um pedido; o chat de afiliado não é de quem
compra. Sem comprador_id não há de quem listar.

NO TIKTOK QUASE NÃO APARECE, POR FALTA DE DADO (medido em 06/11/2026): o índice dele só
recebe os não pagos da rodada do brinde (`indexar.indexar_tiktok_nao_pagos`), 18 pedidos,
e nenhum retrato é montado (`enriquecer.PLATAFORMAS_COM_API`). Dos 1.918 chats, 5 têm a
seção, com o cartão só do índice. Popular o índice muda quem mais o lê no TikTok (as abas,
a ferramenta do agente, o brinde do não pago): é decisão do dono, não deste módulo.

SÓ LEITURA, sem dado pessoal: pedido, status, data, total, o resumo dos itens (nome
do anúncio) e o Bling, com os SKUs dos itens: o resumo não os tem, e é neles que a busca
da lista casa, na tela. Nada aqui grava, e o pedido da conversa não muda. Log só com
ids.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoConversa, AtendimentoPedidoComprador, SituacaoBling
from app.services import links_shopee
from app.services.atendimento import enriquecer, etiqueta_fatos, painel
from app.services.atendimento.constantes import CANAL_CHAT, e_chat_de_afiliado
from app.services.atendimento.etiqueta_fatos import PLATAFORMAS_CHAT_DO_COMPRADOR
from app.services.logistica_rules import TIKTOK_STATUS_LABELS_PT

# Só um cinto: o maior comprador em produção tem 21 pedidos (06/11/2026).
MAX_OUTRAS_COMPRAS = 100

_TEXTO_DO_STATUS = {
    "shopee": enriquecer.STATUS_PEDIDO_SHOPEE,
    "tiktok": TIKTOK_STATUS_LABELS_PT,
}


def aplica(conversa: AtendimentoConversa) -> bool:
    """A conversa é o chat de UM comprador na loja? Fora dele, a lista nem existe."""
    return (
        conversa.plataforma in PLATAFORMAS_CHAT_DO_COMPRADOR
        and conversa.canal == CANAL_CHAT
        and conversa.integration_id is not None
        and bool((conversa.comprador_id or "").strip())
        and not e_chat_de_afiliado(conversa.plataforma, conversa.dados)
    )


def status_texto(plataforma: str | None, status: str | None) -> str | None:
    """O status em português, com os mapas de hoje; código desconhecido, como veio."""
    return _TEXTO_DO_STATUS.get(plataforma or "", {}).get(status or "", status)


async def _situacoes(session: AsyncSession, ids: set[str]) -> dict[str, str]:
    """{id da situação: nome} do catálogo do Bling, numa consulta só."""
    numeros = sorted(int(i) for i in ids if i.isdigit())
    if not numeros:
        return {}
    linhas = await session.execute(
        select(SituacaoBling.id, SituacaoBling.nome).where(SituacaoBling.id.in_(numeros))
    )
    return {str(i): nome for i, nome in linhas.all() if nome}


async def _blings(session: AsyncSession, pedidos: list[str]) -> dict[str, dict[str, Any]]:
    """{pedido: a linha do Bling} — o número, a situação com o texto de hoje, o link e os SKUs."""
    achados = await etiqueta_fatos.pedidos_bling_por_numeroloja(session, pedidos)
    nomes = await _situacoes(session, {p.situacao for p in achados.values() if p.situacao})
    return {
        pedido: {
            "numero": p.numero,
            "situacao_id": p.situacao,
            "situacao": nomes.get(p.situacao or "") or p.situacao,
            "link": painel.link_bling(p.bling_id),
            "skus": list(p.skus_itens),
        }
        for pedido, p in achados.items()
    }


async def outras_compras(
    session: AsyncSession, conversa: AtendimentoConversa, *, so_pedido: str | None = None
) -> dict[str, Any]:
    """`{itens}`: todas, a mais nova primeiro, sem o pedido da conversa.

    `so_pedido` = só aquele (o "atualizar" do cartão). A linha do Bling e os links
    falham sozinhos (SAVEPOINT): o cartão aparece com o que há no índice. Quantas aparecem
    (as 3 últimas e o "Carregar mais", a pesquisada) é da tela: a lista vem inteira, e a
    busca da lista casa nela sem nova consulta.
    """
    loja = conversa.integration_id
    if loja is None or not aplica(conversa):
        return {"itens": []}
    apc = AtendimentoPedidoComprador
    consulta = select(apc.pedido, apc.status, apc.criado_em, apc.total, apc.itens_resumo).where(
        apc.integration_id == loja,
        apc.comprador_id == conversa.comprador_id,
        apc.pedido != conversa.pedido_marketplace,
    )
    if so_pedido is not None:
        consulta = consulta.where(apc.pedido == so_pedido)
    linhas = (
        await session.execute(
            consulta.order_by(apc.criado_em.desc().nulls_last(), apc.pedido.desc()).limit(
                MAX_OUTRAS_COMPRAS
            )
        )
    ).all()
    if not linhas:
        return {"itens": []}
    pedidos = [linha.pedido for linha in linhas]
    sem_bling: dict[str, dict[str, Any]] = {}
    blings, _ = await painel._seguro(
        session, "compras_bling", lambda: _blings(session, pedidos), sem_bling, conversa.id
    )
    ids_shopee = links_shopee.IdsShopee()
    if conversa.plataforma == "shopee":
        ids_shopee, _ = await painel._seguro(
            session,
            "compras_link_shopee",
            lambda: links_shopee.ids_shopee(session, pedidos=[(loja, p) for p in pedidos]),
            ids_shopee,
            conversa.id,
        )
    return {
        "itens": [
            {
                "pedido": linha.pedido,
                "status": linha.status,
                "status_texto": status_texto(conversa.plataforma, linha.status),
                "criado_em": linha.criado_em,
                "total": linha.total,
                "itens_resumo": linha.itens_resumo,
                "bling": blings.get(linha.pedido),
                "link_plataforma": painel.link_na_plataforma(
                    conversa.plataforma, loja, linha.pedido, ids_shopee
                ),
            }
            for linha in linhas
        ],
    }


async def do_comprador(
    session: AsyncSession, conversa: AtendimentoConversa, pedidos: list[str]
) -> list[str]:
    """Os pedidos da lista que são deste comprador nesta loja, na ordem pedida.

    Quem não for, fica fora, sem erro. É a trava do retrato: o número vem da tela.
    """
    if not aplica(conversa):
        return []
    lista = list(dict.fromkeys(p for p in pedidos if p))
    if not lista:
        return []
    apc = AtendimentoPedidoComprador
    achados = await session.execute(
        select(apc.pedido).where(
            apc.integration_id == conversa.integration_id,
            apc.comprador_id == conversa.comprador_id,
            apc.pedido.in_(lista),
        )
    )
    dele = set(achados.scalars().all())
    return [p for p in lista if p in dele]
