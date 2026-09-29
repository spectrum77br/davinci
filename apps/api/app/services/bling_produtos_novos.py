"""Traz para o DaVinci os produtos criados no Bling — sem esperar venda.

Eduardo (29/09/2026): "em devoluções não está aparecendo para colocar no
a009.cd, sendo que ele está ativo". O único caminho que criava produto novo era
o webhook de ESTOQUE do Bling (webhooks.py → auto_create_product_from_bling_run):
produto criado no Bling com estoque 0 e sem movimento nunca chegava. Medido em
29/09: 147 produtos ativos no Bling sem cadastro no DaVinci (a009.cd, os
Uranyx C2/C50/C53/C57/P1/C65 avulsos, kits C68 Plus e WP60, airfryer...).

Dois jeitos de rodar:
- `completo=False` (a cada 15 min): lê as primeiras páginas de "últimos
  incluídos" do Bling (`criterio=1`, do mais novo para o mais antigo) — uma
  consulta por página;
- `completo=True` (1x por dia, rede de segurança): lê todos os produtos ativos.

Cria do mesmo jeito que o webhook (`run_auto_create_product_from_bling`: mesmo
dono, dados do Bling) e acrescenta o vínculo do Bling, como o botão "Importar do
Bling" — sem ele o estoque do produto não é atualizado pela sincronização.
Só produtos ATIVOS; nunca altera nem apaga o que já existe.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, LinkSyncStatus, Product, ProductLink
from app.services import bling_product_create
from app.services.marketplaces.bling import BlingCloudflareError

logger = structlog.get_logger()

# Página do Bling em "últimos incluídos"; 2 páginas = os 200 mais novos.
_POR_PAGINA = 100
_PAGINAS_RECENTES = 2
# Pausa entre criações: cada uma faz 1 GET /produtos/{id} (limite ~3 req/s).
_PAUSA = 0.4
# Lista inteira: ~25 páginas. Depois de cada deploy o product_bling_cost_sync
# (run_at_startup) também lista tudo e o Bling devolve 429 — a 1ª importação de
# 29/09 caiu assim. Pausa entre páginas e nova tentativa por página.
_PAUSA_PAGINA = 1.5
_TENTATIVAS_PAGINA = 6


async def _integracao_bling(session: AsyncSession) -> Integration | None:
    return (
        await session.execute(
            select(Integration).where(Integration.platform == IntegrationPlatform.BLING).limit(1)
        )
    ).scalar_one_or_none()


async def _pagina(client, pagina: int, *, recentes: bool, pausa: float) -> list[dict]:
    params = {"pagina": pagina, "limite": _POR_PAGINA}
    if recentes:
        params["criterio"] = 1  # "últimos incluídos", do mais novo para o mais antigo
    for tentativa in range(_TENTATIVAS_PAGINA):
        try:
            r = await client._request("GET", "/produtos", params=params)
            r.raise_for_status()
            return r.json().get("data") or []
        except (BlingCloudflareError, httpx.TransportError, httpx.TimeoutException,
                httpx.HTTPStatusError, json.JSONDecodeError) as exc:
            # 429/503 (Cloudflare), 502/504, timeout, conexão, 200 com HTML.
            # Erro 4xx de verdade (fora 429) não melhora tentando de novo.
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                raise
            if tentativa == _TENTATIVAS_PAGINA - 1:
                raise
            await asyncio.sleep(pausa * 3 * (tentativa + 1))
    return []


async def _candidatos(client, *, completo: bool, pausa: float = _PAUSA_PAGINA) -> list[dict]:
    out: list[dict] = []
    pagina = 1
    while True:
        itens = await _pagina(client, pagina, recentes=not completo, pausa=pausa)
        out.extend(itens)
        if len(itens) < _POR_PAGINA:
            return out
        if not completo and pagina >= _PAGINAS_RECENTES:
            return out
        pagina += 1
        if pausa:
            await asyncio.sleep(pausa)


async def _garantir_vinculo_bling(
    session: AsyncSession, *, product: Product, integration: Integration, bling_product_id: int
) -> None:
    """Mesmo que routers/products.py::_ensure_bling_link (o botão Importar)."""
    ja = (
        await session.execute(
            select(ProductLink.id).where(
                ProductLink.product_id == product.id,
                ProductLink.platform == IntegrationPlatform.BLING,
            )
        )
    ).scalar_one_or_none()
    if ja is not None:
        return
    session.add(
        ProductLink(
            user_id=integration.user_id,
            product_id=product.id,
            integration_id=integration.id,
            platform=IntegrationPlatform.BLING,
            external_id=str(bling_product_id),
            external_sku=product.sku,
            listing_title=product.name,
            stock=product.stock,
            last_sync_status=LinkSyncStatus.PENDING,
        )
    )
    await session.commit()


async def _reativar(session: AsyncSession, bling_id: int, codigo: str) -> int:
    """Ativo no Bling e inativo aqui (29/09: dg078.pi+a020.pi estava 'I') — a
    busca de Devoluções e as telas só mostram ativo. O Bling é a fonte. Só pelo
    id do Bling: pelo código, poderia reativar uma cópia velha (produto apagado
    e recriado no Bling com o mesmo código) com o estoque de quando foi excluída."""
    linhas = (
        await session.execute(select(Product).where(Product.bling_product_id == bling_id))
    ).scalars().all()
    if not linhas or any((p.situacao or "A") == "A" for p in linhas):
        return 0
    prod = linhas[0]
    logger.info("produto_reativado_pelo_bling", sku=prod.sku, antes=prod.situacao)
    prod.situacao = "A"
    await session.commit()
    return 1


# Trava do "inativar quem saiu do Bling": acima disso numa rodada é anomalia
# (lista incompleta, conta trocada) — não mexe em nada e avisa no log.
_MAX_INATIVAR = 50


async def _inativar_quem_saiu(session: AsyncSession, ativos_bling: list[dict]) -> int:
    """Ativo aqui, com id do Bling, e FORA da lista completa de ativos do Bling
    = foi inativado (ou excluído) lá. Revisão de 29/09: nada rebaixava A→I, e com
    a soma por família para todas as linhas um anúncio de outro lote passaria a
    prometer as peças desse produto — que o robô não consegue usar na venda."""
    ids_ativos = {
        int(a["id"])
        for a in ativos_bling
        if a.get("id") and (a.get("situacao") or "A").strip().upper() in ("A", "ATIVO")
    }
    aqui = (
        await session.execute(
            select(Product).where(
                Product.situacao == "A", Product.bling_product_id.is_not(None)
            )
        )
    ).scalars().all()
    if len(ids_ativos) < 0.9 * len(aqui):
        logger.warning(
            "produtos_bling_inativar_lista_curta", ativos_bling=len(ids_ativos), aqui=len(aqui)
        )
        return 0
    sairam = [p for p in aqui if int(p.bling_product_id) not in ids_ativos]
    if len(sairam) > _MAX_INATIVAR:
        logger.warning(
            "produtos_bling_inativar_demais", quantidade=len(sairam),
            amostra=[p.sku for p in sairam[:20]],
        )
        return 0
    for p in sairam:
        logger.info("produto_inativado_pelo_bling", sku=p.sku, estoque=p.stock)
        p.situacao = "I"
    if sairam:
        await session.commit()
    return len(sairam)


async def importar_produtos_novos(
    session: AsyncSession, *, completo: bool = False, pausa: float = _PAUSA
) -> dict:
    """Cria no DaVinci os produtos ativos do Bling que ainda não existem aqui."""
    resumo = {
        "lidos": 0, "faltando": 0, "criados": 0, "falhas": 0, "reativados": 0,
        "completo": completo,
    }
    integ = await _integracao_bling(session)
    if integ is None:
        return {**resumo, "erro": "sem_integracao_bling"}
    client = await bling_product_create._bling_client_for_user(session)
    if client is None:
        return {**resumo, "erro": "sem_integracao_bling"}

    candidatos = await _candidatos(client, completo=completo, pausa=_PAUSA_PAGINA if pausa else 0)
    resumo["lidos"] = len(candidatos)
    ids = {
        r[0]
        for r in (await session.execute(select(Product.bling_product_id))).all()
        if r[0] is not None
    }
    skus = {
        (r[0] or "").strip().lower() for r in (await session.execute(select(Product.sku))).all()
    }

    criados: list[str] = []
    for raw in candidatos:
        try:
            bling_id = int(raw.get("id"))
        except (TypeError, ValueError):
            continue
        codigo = (raw.get("codigo") or "").strip()
        situacao = (raw.get("situacao") or "").strip().upper()
        if not codigo or situacao not in ("A", "ATIVO", ""):
            continue
        if bling_id in ids or codigo.lower() in skus:
            if completo:
                resumo["reativados"] += await _reativar(session, bling_id, codigo)
            continue
        resumo["faltando"] += 1
        res = await bling_product_create.run_auto_create_product_from_bling(
            session, bling_product_id=bling_id, user_id=integ.user_id
        )
        if not res.get("ok"):
            resumo["falhas"] += 1
            logger.warning(
                "produtos_novos_bling_falhou", bling_product_id=bling_id, sku=codigo,
                erro=res.get("error"),
            )
            continue
        produto = await session.get(Product, UUID(str(res["product_id"])))
        if produto is not None:
            await _garantir_vinculo_bling(
                session, product=produto, integration=integ, bling_product_id=bling_id
            )
            ids.add(bling_id)
            skus.add((produto.sku or "").strip().lower())
        if res.get("created"):
            resumo["criados"] += 1
            criados.append(codigo)
        if pausa:
            await asyncio.sleep(pausa)

    if completo:
        resumo["inativados"] = await _inativar_quem_saiu(session, candidatos)

    if resumo["criados"] or resumo["falhas"] or resumo["reativados"] or resumo.get("inativados"):
        logger.info("produtos_novos_bling", **resumo, skus=criados[:50])
    return resumo
