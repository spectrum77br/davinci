"""Coletor da Amazon da Conferência (07/10/2026) — no servidor, sem AdsPower.

Interface: coletores/__init__.py. Nesta versão a Amazon tem só VENDAS: os
itens dos pedidos do Bling da loja (coletores/vendas_bling.py — pela data do
pedido, valor de tabela sem frete e sem os descontos do pedido, sem os
cancelados na hora da coleta; eletro pelo SKU). As
lojas do Bling da Amazon foram conferidas no dossiê de 07/10/2026: KFA
204438129, Kia 204713113, Poofy 206099015 (Nexus 206064394, desativada).

Ads: o DaVinci ainda não tem acesso à API de Anúncios da Amazon (nenhuma
credencial; os números de Ads da Amazon no Marketing eram do robô de
demonstração). As células de Ads e o % investimento / vendas saem
"aguardando acesso" pelo perfil da plataforma (plataformas.ESTADOS); o
coletor não manda a seção `ads` (e a Amazon não a espera: SECOES_ESPERADAS).
Quando a API for conectada, os totais e o eletro do Ads entram aqui (relatório
spAdvertisedProduct diário por SKU anunciado — o SKU da Amazon é o do DaVinci).

Afiliados e Saldo Ads: "não se aplica" (perfil da plataforma).

Sem loja do Bling ligada não há o que coletar: ColetorError (a coleta vira
`erro`, com a mensagem). O mesmo quando nenhuma semana tem Vendas confiáveis
(loja que o espelho do Bling não conhece, espelho parado — vendas_bling); só
algumas semanas sem → a coleta fica `parcial` com "—" nelas.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.conferencia_shopee.coletores import (
    ColetaDados,
    ColetorError,
    ContaColeta,
    Semana,
    vendas_bling,
)

logger = structlog.get_logger()

SEM_LOJA = (
    "a conta não tem loja do Bling ligada — as Vendas da Amazon saem dos pedidos do Bling "
    "(ligar em Contas)"
)


async def collect(
    conta: ContaColeta, semanas: Sequence[Semana], *, session: AsyncSession
) -> ColetaDados:
    if not conta.bling_loja_id:
        raise ColetorError(SEM_LOJA)
    t0 = time.monotonic()
    avisos: list[str] = []
    vendas = await vendas_bling.vendas_por_semana(
        session,
        conta.bling_loja_id,
        semanas,
        marcar_eletro=conta.grupo == "celular",
        avisos_loja=avisos,
    )
    saida: list[dict[str, Any]] = []
    for s in semanas:
        v = vendas[s]
        semana: dict[str, Any] = dict(s.chave())
        # Sem "vendas" = o zero não seria confiável (espelho do Bling parado):
        # a semana fica "—" e a coleta, `parcial`.
        if "vendas" in v:
            semana["vendas"] = v["vendas"]
            semana["vendas_itens"] = v["vendas_itens"]
        semana["avisos"] = list(v["avisos"])
        saida.append(semana)
    if not any("vendas" in s for s in saida):
        # A Amazon só tem Vendas: nenhuma semana confiável = nada coletado.
        motivos = avisos or [a for s in saida for a in s["avisos"]][:1]
        raise ColetorError("; ".join(motivos) or "Vendas não coletadas")
    duracao = round(time.monotonic() - t0, 1)
    logger.info("conferencia_amazon_conta", conta=conta.nome, duracao_s=duracao)
    return {"versao": 1, "duracao_s": duracao, "chamadas": 0, "semanas": saida, "avisos": avisos}
