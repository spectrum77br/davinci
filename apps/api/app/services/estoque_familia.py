"""Família de estoque: quais SKUs são o MESMO produto em lotes diferentes.

Eduardo (22/09/2026): "o anúncio do .ci tem que mostrar o total, se somarmos
dá 2 mil" — para o anúncio nunca zerar enquanto houver peça em outro lote.

A regra que ele definiu: tira o lote de cada pedaço e compara o resto.
    dg053.ci+a001.ci  →  dg053+a001
    dg053.sp+a001.sp  →  dg053+a001   → mesma família, soma
    dg053.ci+a003.ci  →  dg053+a003   → OUTRA família, não soma

POR QUE O KIT NÃO SOMA COM O SIMPLES: o Bling dá ao kit o saldo do menor
componente, então `dg053.ci` (210 aparelhos) aparece também em `dg053.ci+a001.ci`
(210), `dg053.ci+a003.ci` (210)... São as MESMAS 210 peças mostradas cinco
vezes. Somar tudo daria 3.252 aparelhos quando existem 1.183 — dois mil
celulares que não existem. Por isso a chave inclui TODOS os componentes.

Lotes de venda (Eduardo, 22/09): ci, ra, sa, pi, sp. Ficam de fora `cd`
(Centro de Distribuição — é o que ainda não foi distribuído), `us` (usado) e
qualquer sufixo desconhecido, que simplesmente não forma família.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Product
from app.services.sku_tags import SUFFIX_TAGS

# Só estes lotes entram na conta do que é publicado.
LOTES_DE_VENDA = frozenset({"ci", "ra", "sa", "pi", "sp"})


def lote_de(pedaco: str) -> str | None:
    """Sufixo de lote de UM pedaço do SKU (`dg053.ci` → `ci`), ou None.

    Mesma regra do resto do sistema (`SUFFIX_TAGS`): número não é lote, senão
    `b009.8.12.20.24` (tamanhos) e `uaf001m1.110` (voltagem) virariam família.
    """
    if "." not in pedaco:
        return None
    tail = pedaco.rsplit(".", 1)[1].strip().lower()
    return tail if tail in SUFFIX_TAGS else None


def chave_familia(sku: str | None) -> str | None:
    """Chave da família: o SKU inteiro sem os lotes, ou None se não formar.

    Devolve None quando: não há pedaço com lote; os pedaços têm lotes
    DIFERENTES (kit misturado — conservador, não dá para saber de que lote é a
    peça); ou algum lote está fora dos lotes de venda (`cd`, `us`).
    """
    low = (sku or "").strip().lower()
    if not low or low.startswith("fake."):
        return None
    pedacos = [p.strip() for p in low.split("+") if p.strip()]
    if not pedacos:
        return None
    lotes = {lote_de(p) for p in pedacos}
    lotes.discard(None)
    if len(lotes) != 1:
        return None  # sem lote nenhum, ou kit com lotes misturados
    lote = next(iter(lotes))
    if lote not in LOTES_DE_VENDA:
        return None
    sem_lote = [p[: -(len(lote) + 1)] if lote_de(p) == lote else p for p in pedacos]
    return "+".join(sem_lote)


def irmaos(sku: str) -> list[str]:
    """Os SKUs da mesma família, um por lote de venda, em ordem fixa.

    `dg053.ci+a001.ci` → dg053.ci+a001.ci, dg053.pi+a001.pi, dg053.ra+a001.ra,
    dg053.sa+a001.sa, dg053.sp+a001.sp. Quem não existe no catálogo some na
    consulta — aqui só se monta o nome.
    """
    base = chave_familia(sku)
    if base is None:
        return []
    pedacos = base.split("+")
    return ["+".join(f"{p}.{lote}" for p in pedacos) for lote in sorted(LOTES_DE_VENDA)]


async def saldo_publicavel(
    session: AsyncSession,
    product: Product,
    *,
    cache: dict[str, int] | None = None,
) -> int:
    """Quanto este produto deve MOSTRAR no anúncio.

    Sem a soma ligada, é o saldo dele mesmo — exatamente como sempre foi. Com a
    soma ligada, é o total da família, para o anúncio de um lote não zerar
    enquanto houver peça em outro. O que sai do estoque na venda continua sendo
    decidido pelo lote do anúncio ou pela prioridade; isto aqui só muda o NÚMERO
    publicado.

    Três freios, todos ajustáveis sem mexer em código:

    `estoque_familia_ativo`     desligado por padrão.
    `estoque_familia_prefixos`  limita a soma a certas linhas (começamos no A17);
                                vazio = todas as famílias.
    `estoque_familia_minimo`    piso de segurança. Somar faz TODOS os anúncios da
                                família mostrarem o mesmo número, então uma
                                família de 1 peça em 14 anúncios passa a oferecer
                                a mesma peça 14 vezes. Abaixo do piso a família
                                não soma e cada anúncio volta a mostrar o dele.

    O dono (`user_id`) é ignorado de propósito (Eduardo, 22/09/2026: "pode
    juntar"): dg053.ci e dg053.sp são o mesmo celular no mesmo galpão, ainda que
    registrados em nomes diferentes.
    """
    proprio = max(0, int(product.stock or 0))
    s = get_settings()
    if not getattr(s, "estoque_familia_ativo", False):
        return proprio

    base = chave_familia(product.sku)
    if base is None:
        return proprio

    prefixos = tuple(
        p.strip().lower()
        for p in (getattr(s, "estoque_familia_prefixos", "") or "").split(",")
        if p.strip()
    )
    if prefixos and not base.split("+")[0].startswith(prefixos):
        return proprio

    if cache is not None and base in cache:
        total = cache[base]
    else:
        nomes = irmaos(product.sku or "")
        linhas = (
            await session.execute(
                select(Product.stock).where(
                    func.lower(Product.sku).in_(nomes), Product.situacao == "A"
                )
            )
        ).all()
        total = sum(max(0, int(stock or 0)) for (stock,) in linhas)
        if cache is not None:
            cache[base] = total

    minimo = int(getattr(s, "estoque_familia_minimo", 0) or 0)
    if total < minimo:
        return proprio
    return max(total, proprio)
