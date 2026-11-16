"""Código de barras (GTIN) que a Shopee pede para mudar o envio (16/11/2026).

O caso: 10 anúncios Shopee com a Entrega Direta ligada e sem peça no .sp —
o sistema queria desligar e a Shopee recusava toda vez com "The GTIN code is
required for this category. Please fill in it." (categoria de celular: o
código é obrigatório, e "00" — "produto sem GTIN" — só vale em categoria com
regra Flexible). O Seller Center também não salva nada com o campo vazio. Em
13/11 três foram preenchidos à mão; agora eram ~140 variações.

Decisão do Vinicius (16/11/2026, opção A): o sistema preenche sozinho.
De onde sai o número de cada variação vazia, nesta ordem:
  1. o código que o MESMO celular já usa — nas outras variações do anúncio
     e nos anúncios das outras lojas Shopee (o celular = o começo do SKU,
     "dg019" de "dg019.ra+a003.ra": as lojas mudam o sufixo e o acessório
     do kit, o aparelho é o mesmo). Só vale SKU que é produto do DaVinci
     ("xx1", sem produto, não identifica aparelho nenhum);
  2. sem isso, o código de outra variação do MESMO anúncio: a de nome igual,
     depois a da mesma primeira opção (a cor/modelo), depois a mais usada.
Entre os candidatos, primeiro os códigos da GS1 Brasil (789/790 — os
7908855… são da empresa; os de 12 dígitos que aparecem nas lojas mudam de
loja para loja, cara de inventado), depois o mais usado. Só entra código com
o dígito verificador certo. Nada achado: a variação fica vazia e a tela diz
qual — continua com a pessoa, no Seller Center.

Escrita: `update_model` (até 50 variações por chamada; `model_sku` é
obrigatório, vai o que já está lá, e o peso/medidas lidos vão iguais para a
Shopee não voltar ao padrão do anúncio). Anúncio sem variação: `update_item`
com o `gtin_code`. Só roda dentro da escrita do Flex da Shopee (motor em
piloto/ativo e `flex_shopee_escrita`), e só depois da recusa por GTIN.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Listing, Product, ProductLink

logger = structlog.get_logger(__name__)

# Recusa da Shopee por código vazio ("The GTIN code is required for this
# category" no update_item; a doc do update_model diz "is mandatory").
_RECUSA = ("gtin code is required", "gtin code is mandatory")
# Prefixos da GS1 Brasil.
_PREFIXOS_BR = ("789", "790")
# Anúncios de outras lojas lidos na Shopee por preenchimento (um
# `get_model_list` cada), e quantos bastam por celular.
MAX_ANUNCIOS_LIDOS = 12
_ANUNCIOS_POR_CELULAR = 2
_LOTE_MODELOS = 50

FONTE_MESMO_CELULAR = "mesmo celular"
FONTE_MESMO_NOME = "variação de mesmo nome"
FONTE_MESMA_OPCAO = "variação da mesma opção"
FONTE_DO_ANUNCIO = "outra variação do anúncio"


def recusa_por_gtin(detalhe: str | None) -> bool:
    d = (detalhe or "").lower()
    return any(t in d for t in _RECUSA)


def valido(codigo: Any) -> bool:
    """GTIN-8/12/13/14 com o dígito verificador certo (GS1: pesos 3 e 1 a
    partir da direita, sem o verificador)."""
    s = str(codigo or "").strip()
    if len(s) not in (8, 12, 13, 14) or not s.isdigit() or set(s) == {"0"}:
        return False
    corpo, dv = s[:-1], int(s[-1])
    soma = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(corpo)))
    return (10 - soma % 10) % 10 == dv


def vazio(codigo: Any) -> bool:
    return not str(codigo or "").strip()


def celular_do_sku(sku: Any) -> str | None:
    """'dg019.ra+a003.ra' → 'dg019'; 'z0102' → 'z0102'; vazio → None."""
    s = str(sku or "").strip().lower()
    if not s:
        return None
    return s.split("+", 1)[0].split(".", 1)[0].strip() or None


def melhor(candidatos: Counter[str]) -> str | None:
    validos = [(c, n) for c, n in candidatos.items() if valido(c)]
    if not validos:
        return None
    validos.sort(key=lambda cn: (not cn[0].startswith(_PREFIXOS_BR), -cn[1], cn[0]))
    return validos[0][0]


def _primeira_opcao(m: Mapping[str, Any]) -> int | None:
    ti = m.get("tier_index")
    if isinstance(ti, list) and ti and isinstance(ti[0], int):
        return ti[0]
    return None


@dataclass
class Escolha:
    preencher: list[tuple[dict, str, str]] = field(default_factory=list)  # (modelo, código, fonte)
    sem_codigo: list[dict] = field(default_factory=list)


def escolher(
    modelos: Iterable[Mapping[str, Any]],
    de_fora: Mapping[str, Counter[str]],
    celulares_validos: Collection[str],
) -> Escolha:
    """O código de cada variação VAZIA (PURA). `de_fora`: celular → códigos
    vistos nas outras lojas; `celulares_validos`: celulares de SKU que é
    produto do DaVinci (o resto não identifica aparelho)."""
    lista = [dict(m) for m in modelos]
    cheias = [m for m in lista if valido(m.get("gtin_code"))]
    out = Escolha()
    for m in lista:
        if not vazio(m.get("gtin_code")):
            continue
        cel = celular_do_sku(m.get("model_sku"))
        if cel is not None and cel not in celulares_validos:
            cel = None
        tentativas: list[tuple[str, Counter[str]]] = []
        if cel is not None:
            mesmo = Counter(
                str(o["gtin_code"]).strip()
                for o in cheias
                if celular_do_sku(o.get("model_sku")) == cel
            )
            mesmo.update(de_fora.get(cel) or Counter())
            tentativas.append((FONTE_MESMO_CELULAR, mesmo))
        nome = str(m.get("model_name") or "").strip().lower()
        if nome:
            tentativas.append(
                (
                    FONTE_MESMO_NOME,
                    Counter(
                        str(o["gtin_code"]).strip()
                        for o in cheias
                        if str(o.get("model_name") or "").strip().lower() == nome
                    ),
                )
            )
        opcao = _primeira_opcao(m)
        if opcao is not None:
            tentativas.append(
                (
                    FONTE_MESMA_OPCAO,
                    Counter(
                        str(o["gtin_code"]).strip() for o in cheias if _primeira_opcao(o) == opcao
                    ),
                )
            )
        tentativas.append((FONTE_DO_ANUNCIO, Counter(str(o["gtin_code"]).strip() for o in cheias)))
        for fonte, cand in tentativas:
            codigo = melhor(cand)
            if codigo is not None:
                out.preencher.append((m, codigo, fonte))
                break
        else:
            out.sem_codigo.append(m)
    return out


def payload_modelo(m: Mapping[str, Any], codigo: str) -> dict[str, Any]:
    """Uma variação do `update_model`: o código novo, o `model_sku`
    (obrigatório: vai o que já está) e o peso/medidas lidos, iguais."""
    out: dict[str, Any] = {
        "model_id": int(m["model_id"]),
        "model_sku": str(m.get("model_sku") or ""),
        "gtin_code": codigo,
    }
    try:
        peso = float(m.get("weight") or 0)
    except (TypeError, ValueError):
        peso = 0.0
    if peso > 0:
        out["weight"] = peso
    dim = m.get("dimension")
    if isinstance(dim, Mapping):
        try:
            medidas = {
                k: int(dim[k]) for k in ("package_height", "package_length", "package_width")
            }
        except (KeyError, TypeError, ValueError):
            medidas = {}
        if medidas and all(v > 0 for v in medidas.values()):
            out["dimension"] = medidas
    return out


def nome_da_variacao(m: Mapping[str, Any]) -> str:
    return str(m.get("model_name") or m.get("model_sku") or m.get("model_id") or "?")


@dataclass
class Preenchimento:
    """O que o preenchimento fez. `gravados`: variações que receberam código;
    `sem_codigo`: nomes das que ficaram vazias; `erro`: a Shopee recusou a
    gravação (ou a leitura falhou)."""

    gravados: int = 0
    sem_codigo: list[str] = field(default_factory=list)
    erro: str | None = None
    codigos: list[tuple[str, str, str]] = field(default_factory=list)  # (variação, código, fonte)

    def nota(self) -> str:
        if not self.gravados:
            return ""
        usados = sorted({c for _, c, _ in self.codigos})
        return (
            f"código de barras preenchido pelo sistema em {self.gravados} "
            f"variação(ões): {', '.join(usados)}"
        )[:400]

    def faltou(self) -> str:
        if self.erro:
            return f"o sistema tentou preencher o código de barras e a Shopee recusou: {self.erro}"[
                :300
            ]
        if not self.sem_codigo:
            return ""
        nomes = ", ".join(self.sem_codigo[:8]) + ("…" if len(self.sem_codigo) > 8 else "")
        return (
            f"sem código de barras conhecido para {len(self.sem_codigo)} variação(ões): {nomes}"
        )[:300]


async def _celulares_validos(session: AsyncSession, skus: Iterable[Any]) -> set[str]:
    """Celulares (começo do SKU) das variações cujo SKU é produto do DaVinci."""
    alvo = {str(s).strip().lower() for s in skus if str(s or "").strip()}
    if not alvo:
        return set()
    rows = await session.execute(
        select(func.lower(Product.sku)).where(func.lower(Product.sku).in_(alvo))
    )
    return {c for (sku,) in rows.all() if (c := celular_do_sku(sku))}


async def _codigos_guardados(
    session: AsyncSession, celulares: Collection[str]
) -> dict[str, Counter[str]]:
    """Os códigos que a importação de anúncios já guardou (`listings.raw_data`,
    uma variação por anúncio) — sem ir à Shopee."""
    out: dict[str, Counter[str]] = {}
    if not celulares:
        return out
    sku = Listing.raw_data["model"]["model_sku"].astext
    gtin = Listing.raw_data["model"]["gtin_code"].astext
    cel = func.split_part(func.split_part(func.lower(sku), "+", 1), ".", 1)
    rows = await session.execute(
        select(cel, gtin).where(
            Listing.platform == IntegrationPlatform.SHOPEE,
            cel.in_(list(celulares)),
            func.coalesce(gtin, "") != "",
        )
    )
    for c, g in rows.all():
        if valido(g):
            out.setdefault(c, Counter())[str(g).strip()] += 1
    return out


async def _anuncios_das_outras_lojas(
    session: AsyncSession, celulares: Collection[str], *, integration_id: UUID, item_id: str
) -> list[tuple[UUID, str, set[str]]]:
    """(loja, anúncio, celulares que ele tem) dos anúncios Shopee VIVOS com o
    mesmo celular — os que cobrem mais celulares primeiro, até
    `_ANUNCIOS_POR_CELULAR` por celular e `MAX_ANUNCIOS_LIDOS` no total."""
    if not celulares:
        return []
    cel = func.split_part(func.split_part(func.lower(Product.sku), "+", 1), ".", 1)
    rows = await session.execute(
        select(ProductLink.integration_id, ProductLink.external_id, cel)
        .join(Product, Product.id == ProductLink.product_id)
        .join(Integration, Integration.id == ProductLink.integration_id)
        .where(
            ProductLink.platform == IntegrationPlatform.SHOPEE,
            ProductLink.morto_desde.is_(None),
            Integration.archived_at.is_(None),
            cel.in_(list(celulares)),
        )
        .distinct()
    )
    por_anuncio: dict[tuple[UUID, str], set[str]] = {}
    for iid, ext, c in rows.all():
        if iid == integration_id and str(ext) == str(item_id):
            continue
        por_anuncio.setdefault((iid, str(ext)), set()).add(c)
    escolhidos: list[tuple[UUID, str, set[str]]] = []
    cobertos: Counter[str] = Counter()
    for (iid, ext), cels in sorted(por_anuncio.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        if len(escolhidos) >= MAX_ANUNCIOS_LIDOS:
            break
        if all(cobertos[c] >= _ANUNCIOS_POR_CELULAR for c in cels):
            continue
        escolhidos.append((iid, ext, cels))
        cobertos.update(cels)
    return escolhidos


async def codigos_de_fora(
    session: AsyncSession,
    celulares: Collection[str],
    *,
    integration_id: UUID,
    item_id: str,
    cliente_de: Callable[[UUID], Awaitable[Any]],
) -> dict[str, Counter[str]]:
    """celular → códigos vistos nas outras lojas: os guardados na importação e
    os lidos agora (`get_model_list`) nos anúncios vinculados ao mesmo
    celular. Leitura que falha é pulada."""
    out = await _codigos_guardados(session, celulares)
    for iid, ext, _cels in await _anuncios_das_outras_lojas(
        session, celulares, integration_id=integration_id, item_id=item_id
    ):
        try:
            cli = await cliente_de(iid)
            if cli is None:
                continue
            modelos = (await cli.get_model_list(int(ext))).get("model") or []
        except Exception as exc:  # noqa: BLE001 — uma loja que falha não para as outras
            logger.info("flex_gtin_leitura_falhou", item=ext, erro=str(exc)[:200])
            continue
        for m in modelos:
            c = celular_do_sku(m.get("model_sku"))
            g = str(m.get("gtin_code") or "").strip()
            if c in celulares and valido(g):
                out.setdefault(c, Counter())[g] += 1
    return out


async def preencher(
    session: AsyncSession,
    cliente: Any,
    *,
    integration_id: UUID,
    item_id: str,
    cliente_de: Callable[[UUID], Awaitable[Any]],
) -> Preenchimento:
    """Lê as variações do anúncio, escolhe o código de cada uma vazia e grava.
    Nunca levanta: o que deu errado vai em `erro`."""
    res = Preenchimento()
    try:
        modelos = (await cliente.get_model_list(int(item_id))).get("model") or []
    except Exception as exc:  # noqa: BLE001
        res.erro = f"não deu para ler as variações: {str(exc)[:200]}"
        return res
    if not modelos:
        return await _preencher_sem_variacao(
            session, cliente, integration_id=integration_id, item_id=item_id, cliente_de=cliente_de
        )
    vazias = [m for m in modelos if vazio(m.get("gtin_code"))]
    if not vazias:
        return res
    validos = await _celulares_validos(session, (m.get("model_sku") for m in vazias))
    de_fora = await codigos_de_fora(
        session, validos, integration_id=integration_id, item_id=item_id, cliente_de=cliente_de
    )
    escolha = escolher(modelos, de_fora, validos)
    res.sem_codigo = [nome_da_variacao(m) for m in escolha.sem_codigo]
    lote = escolha.preencher
    for i in range(0, len(lote), _LOTE_MODELOS):
        parte = lote[i : i + _LOTE_MODELOS]
        try:
            await cliente.gravar_gtin_variacoes(
                int(item_id), [payload_modelo(m, codigo) for m, codigo, _ in parte]
            )
        except Exception as exc:  # noqa: BLE001
            res.erro = str(exc)[:200]
            break
        res.gravados += len(parte)
        res.codigos.extend((nome_da_variacao(m), c, f) for m, c, f in parte)
    logger.info(
        "flex_gtin_preenchido",
        item=item_id,
        gravados=res.gravados,
        sem_codigo=len(res.sem_codigo),
        erro=res.erro,
        codigos=[(m.get("model_id"), c, f) for m, c, f in lote][:60],
    )
    return res


async def _preencher_sem_variacao(
    session: AsyncSession,
    cliente: Any,
    *,
    integration_id: UUID,
    item_id: str,
    cliente_de: Callable[[UUID], Awaitable[Any]],
) -> Preenchimento:
    """Anúncio sem variação: o código vai no próprio anúncio (`update_item`),
    pelo celular do SKU do anúncio."""
    res = Preenchimento()
    try:
        itens = await cliente.get_item_base_info([int(item_id)])
    except Exception as exc:  # noqa: BLE001
        res.erro = f"não deu para ler o anúncio: {str(exc)[:200]}"
        return res
    item = itens[0] if itens else {}
    if not vazio(item.get("gtin_code")):
        return res
    sku = item.get("item_sku")
    validos = await _celulares_validos(session, [sku])
    cel = celular_do_sku(sku) if validos else None
    codigo = None
    if cel is not None:
        de_fora = await codigos_de_fora(
            session, {cel}, integration_id=integration_id, item_id=item_id, cliente_de=cliente_de
        )
        codigo = melhor(de_fora.get(cel) or Counter())
    if codigo is None:
        res.sem_codigo = [str(sku or "o anúncio")]
        return res
    try:
        await cliente.gravar_gtin_anuncio(int(item_id), codigo)
    except Exception as exc:  # noqa: BLE001
        res.erro = str(exc)[:200]
        return res
    res.gravados = 1
    res.codigos.append((str(sku), codigo, FONTE_MESMO_CELULAR))
    logger.info("flex_gtin_preenchido", item=item_id, gravados=1, codigos=[(None, codigo)])
    return res
