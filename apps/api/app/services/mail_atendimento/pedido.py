"""O pedido e o protocolo que o e-mail cita (RF5 "como prender ao pedido" e RF6).

PEDIDO: o número é procurado primeiro no ASSUNTO e depois no texto NOVO (sem
a citação do histórico), com os formatos de PRODUÇÃO (`contexto.py`) e só os
da plataforma da pasta:

  ML, AliExpress e Magalu — 16 dígitos (o pack/order do ML também);
  TikTok                  — 18 dígitos;
  Amazon                  — 3-7-7 (701-1234567-1234567);
  Shopee                  — AAMMDD + 8 letras/dígitos (com pelo menos uma letra);
  Temu                    — PO-999-…

CPF, CEP, telefone e rastreio não casam nesses formatos (e o CPF que
coincidir com um número citado nunca vira pedido: só formato de pedido conta).
O número só LIGA se o pedido existir NAQUELA loja (`existe_na_loja`): na
conversa dela, no índice de pedidos do comprador ou no espelho do Bling da
loja. O número que não existe vira "citado, não encontrado" (e-mail sem
vínculo). O de OUTRA plataforma que a da pasta acende "pasta × conteúdo".

PROTOCOLO (RF6, decidido em 01/10): `{marca}{tipo}-{AA}-{NNNN}` — U, C, 7,
L × S (SAC), A (Atacado), DS (Dúvidas e sugestões). O DaVinci LÊ o protocolo
do e-mail; nunca gera outro. A Locagil não tem Atacado. Veio do wt-tuta
(`tuta/pedido.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoConversa,
    AtendimentoPedidoComprador,
    BlingOrder,
    Store,
    StoreInfo,
)
from app.services.atendimento.validador import plano_de

_LONGO_16 = re.compile(r"(?<![\d-])\d{16}(?![\d-])")
_LONGO_18 = re.compile(r"(?<![\d-])\d{18}(?![\d-])")
_AMAZON = re.compile(r"(?<![\d-])\d{3}-\d{7}-\d{7}(?![\d-])")
_TEMU = re.compile(r"\bpo-\d{3}-\d{8,20}\b")
_SHOPEE = re.compile(r"(?<![a-z0-9])\d{6}[a-z0-9]{8}(?![a-z0-9])")

FORMATOS: dict[str, tuple[re.Pattern, ...]] = {
    "ml": (_LONGO_16,),
    "aliexpress": (_LONGO_16,),
    "magalu": (_LONGO_16,),
    "tiktok": (_LONGO_18,),
    "amazon": (_AMAZON,),
    "shopee": (_SHOPEE,),
    "temu": (_TEMU,),
}
# Os formatos que só UMA plataforma usa: o número de uma delas no e-mail de
# outra pasta é sinal de "pasta × conteúdo" (C12). Os 16 dígitos são de três.
_DISTINTIVOS: dict[str, tuple[re.Pattern, ...]] = {
    "amazon": (_AMAZON,),
    "shopee": (_SHOPEE,),
    "temu": (_TEMU,),
    "tiktok": (_LONGO_18,),
}
MAX_CITADOS = 10
# Mais que isto de pedidos que EXISTEM = resumo (diário, marketing): só histórico.
MAX_PEDIDOS_POR_EMAIL = 3


def _formato(plataforma: str, bruto: str) -> str:
    if plataforma in ("shopee", "temu"):
        return bruto.upper()
    return bruto


def _valido(plataforma: str, numero: str) -> bool:
    if plataforma == "shopee":
        # AAMMDD + algo com pelo menos uma letra (só dígitos é outra coisa).
        return bool(re.search(r"[a-z]", numero[6:].lower()))
    return True


def citados(plataforma: str | None, *textos: str | None) -> list[str]:
    """Os números de pedido no formato DA PLATAFORMA, na ordem (assunto antes), sem repetir."""
    padroes = FORMATOS.get(plataforma or "", ())
    saida: list[str] = []
    for bruto in textos:
        plano = plano_de(bruto or "")
        if not plano:
            continue
        for padrao in padroes:
            for m in padrao.finditer(plano):
                numero = m.group(0)
                if not _valido(plataforma or "", numero):
                    continue
                numero = _formato(plataforma or "", numero)
                if numero not in saida:
                    saida.append(numero)
                if len(saida) >= MAX_CITADOS:
                    return saida
    return saida


def de_outra_plataforma(plataforma: str | None, *textos: str | None) -> list[str]:
    """As plataformas cujo formato EXCLUSIVO aparece no e-mail e não são a da pasta (C12)."""
    outras: list[str] = []
    plano = " ".join(plano_de(t or "") for t in textos)
    for outra, padroes in _DISTINTIVOS.items():
        if outra == plataforma or outra in outras:
            continue
        for padrao in padroes:
            achou = [m.group(0) for m in padrao.finditer(plano) if _valido(outra, m.group(0))]
            if achou:
                outras.append(outra)
                break
    return outras


async def existe_na_loja(
    session: AsyncSession,
    *,
    integration_id,
    plataforma: str,
    numero: str,
    store_info_id=None,
) -> bool:
    """O pedido existe NESTA loja? Conversa dela, índice do comprador ou Bling da loja.

    No Bling, o pedido de uma loja (Empresas › loja ligada à integração) que
    é OUTRA não conta; o pedido sem loja no espelho conta (o número longo de
    marketplace não se repete entre plataformas).

    A loja SEM integração (`store_info_id`, a ficha: Temu, AliExpress…): só a
    conversa de e-mail dela e o espelho do Bling da LOJA DO BLING da ficha
    (`store_info.bling_store_id` = `bling_orders.loja`). Sem a loja do Bling
    na ficha, o número nunca liga sozinho (fica "sem vínculo": uma pessoa liga).
    """
    if not numero:
        return False
    if integration_id is None:
        if store_info_id is None:
            return False
        return await _existe_na_ficha(session, store_info_id, numero)
    da_conversa = await session.scalar(
        select(AtendimentoConversa.id)
        .where(
            AtendimentoConversa.integration_id == integration_id,
            or_(
                AtendimentoConversa.pedido_marketplace == numero,
                AtendimentoConversa.dados["pack_id"].astext == numero,
                AtendimentoConversa.dados["order_id"].astext == numero,
            ),
        )
        .limit(1)
    )
    if da_conversa is not None:
        return True
    do_indice = await session.scalar(
        select(AtendimentoPedidoComprador.id)
        .where(
            AtendimentoPedidoComprador.integration_id == integration_id,
            AtendimentoPedidoComprador.pedido == numero,
        )
        .limit(1)
    )
    if do_indice is not None:
        return True
    linhas = (
        await session.execute(
            select(BlingOrder.store_id, Store.integration_id)
            .outerjoin(Store, Store.id == BlingOrder.store_id)
            .where(BlingOrder.numeroloja == numero)
            .limit(20)
        )
    ).all()
    return any(store_id is None or integ == integration_id for store_id, integ in linhas)


async def _existe_na_ficha(session: AsyncSession, store_info_id, numero: str) -> bool:
    da_conversa = await session.scalar(
        select(AtendimentoConversa.id)
        .where(
            AtendimentoConversa.integration_id.is_(None),
            AtendimentoConversa.dados["mail"]["store_info_id"].astext == str(store_info_id),
            AtendimentoConversa.pedido_marketplace == numero,
        )
        .limit(1)
    )
    if da_conversa is not None:
        return True
    ficha = await session.get(StoreInfo, store_info_id)
    loja_do_bling = (ficha.bling_store_id or "").strip() if ficha is not None else ""
    if not loja_do_bling:
        return False
    return (
        await session.scalar(
            select(BlingOrder.id)
            .where(BlingOrder.numeroloja == numero, BlingOrder.loja == loja_do_bling)
            .limit(1)
        )
    ) is not None


# ── Protocolo dos sites (RF6) ─────────────────────────────────────────────

_RE_PROTOCOLO = re.compile(r"(?<![A-Z0-9])([UC7L])(DS|S|A)-(\d{2})-(\d{4,})(?!\d)")
TIPO_DO_PREFIXO = {"S": "sac", "A": "atacado", "DS": "duvidas"}
# A letra da marca → os slugs da marca no cadastro (o de produção primeiro).
MARCA_DA_LETRA: dict[str, tuple[str, ...]] = {
    "U": ("uranyx",),
    "C": ("charlots-park", "charlots"),
    "7": ("7buyers",),
    "L": ("locagil",),
}
# A Locagil só tem SAC e Dúvidas e sugestões.
SEM_ATACADO = frozenset({"L"})


@dataclass(frozen=True)
class Protocolo:
    numero: str
    letra: str
    tipo: str  # sac | atacado | duvidas
    valido: bool  # False = "LA-…" (a Locagil não tem atacado)


def protocolo(*textos: str | None) -> Protocolo | None:
    """O primeiro protocolo do assunto (depois do texto): "[US-26-0014] Troca" → US-26-0014."""
    for texto in textos:
        for m in _RE_PROTOCOLO.finditer((texto or "").upper()):
            letra, prefixo, ano, seq = m.groups()
            return Protocolo(
                numero=f"{letra}{prefixo}-{ano}-{seq}",
                letra=letra,
                tipo=TIPO_DO_PREFIXO[prefixo],
                valido=not (letra in SEM_ATACADO and prefixo == "A"),
            )
    return None
