"""Vigia do Flex (robô da Ouvidoria, 12/11/2026).

Vinicius, 12/11/2026, depois de dois pedidos de Entrega Direta de um Apple Watch
sem peça em São Bernardo (Atlas Shopee): "consegue criar um robô e colocar em
Ouvidoria › Robôs… ele vai olhar e vai me avisar, o Cairo: não estou conseguindo
desativar os anúncios tal… se preciso ir lá e desativar?"

O robô do Flex (services/flex_motor) desliga sozinho, a cada 15 min, o anúncio com
Flex/Entrega Direta ligado sem peça no local de saída. Ele NÃO consegue quando a
plataforma recusa — na Shopee, anúncio em promoção ("Cannot update logistic info
because the product has promotion") ou sem GTIN — e aí o anúncio continua vendendo
Flex sem peça até uma pessoa agir. Este vigia roda depois das rodadas do Flex e
abre uma ocorrência por anúncio preso, dizendo o motivo e o que fazer (tirar da
promoção, preencher o GTIN, desligar à mão). Fecha sozinha quando o anúncio
desliga.

Também cobra a LISTA de anúncios da Shopee (importação de 1h30,
services/listings_import.importar_anuncios_shopee_sweep): o robô do Flex só confere
anúncio que o DaVinci conhece — lista parada = anúncio novo invisível.

Só olha as contas em que o robô do Flex mexe de verdade (na lista do Flex, Flex
ativo na conta, ML saindo da cidade do local) e só com o Flex em piloto/ativo (em
"observar" ninguém desliga nada — não é preso, é o modo). Nasce `silencioso`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import (
    FlexAnuncioEstado,
    FlexConta,
    Integration,
    Listing,
    OuvidoriaRobo,
    ProductLink,
)
from app.services import flex_config, flex_local, flex_motor, flex_textos, ouvidoria
from app.services.advisory_lock import SYNC_NAMESPACE

logger = structlog.get_logger()

ROBO = "vigia_flex"
_SWEEP_LOCK_KEY = 0x76666C78  # ascii "vflx"
SP = ZoneInfo("America/Sao_Paulo")
LINK = "/logistica?tab=flex&sub=anuncios"
_TENTATIVAS_MINIMAS = 2
_HORAS_IMPORTACAO = 4

# Motivos da plataforma que NÃO passam sozinhos: avisa já na 1ª recusa.
TIPO_PROMOCAO = "promocao"
TIPO_GTIN = "gtin"
TIPO_OUTRO = "outro"


def tipo_do_erro(erro: str | None) -> str:
    e = (erro or "").lower()
    if "promo" in e:
        return TIPO_PROMOCAO
    if "gtin" in e:
        return TIPO_GTIN
    return TIPO_OUTRO


def _hora(dt: datetime | None) -> str:
    return dt.astimezone(SP).strftime("%d/%m %H:%M") if dt else "—"


def _erro_limpo(erro: str | None) -> str:
    """'desligar: 200 em promoção na Shopee: X' → 'em promoção na Shopee: X'."""
    e = (erro or "").strip()
    for prefixo in ("desligar: ", "ligar: "):
        if e.startswith(prefixo):
            e = e[len(prefixo) :]
    if e[:4].strip().isdigit():  # status HTTP na frente ("200 …")
        e = e[4:].strip()
    return e


def _acao(tipo: str, plataforma: str, external_id: str, erro: str) -> str:
    onde = (
        f"Seller Center › Produtos › anúncio {external_id} "
        f"(https://seller.shopee.com.br/portal/product/{external_id})"
        if plataforma == "shopee"
        else f"painel do Mercado Livre › Anúncios › {external_id}"
    )
    if tipo == TIPO_PROMOCAO:
        return (
            f"Tirar o anúncio da promoção ({erro}) — sem promoção o sistema desliga sozinho "
            f"em até 15 min. Enquanto a promoção durar, a Shopee não deixa mudar o envio. "
            f"Onde: {onde}"
        )
    if tipo == TIPO_GTIN:
        return (
            "Preencher o código de barras (GTIN) do anúncio — a Shopee só aceita mexer no "
            f"anúncio com o GTIN. Depois o sistema desliga sozinho. Onde: {onde}"
        )
    canal = "a Entrega Direta" if plataforma == "shopee" else "o Flex"
    return f"Desligar {canal} do anúncio à mão. Onde: {onde}"


async def _contas_prontas(session: AsyncSession) -> dict[UUID, Integration]:
    """As contas em que o robô do Flex ESCREVE: na lista do Flex, ativas,
    Flex ligado na conta, a Shopee com a escrita ligada e o ML saindo da
    cidade do local."""
    permitidas = await flex_motor.integracoes_permitidas(session, flex_config.contas())
    if not permitidas:
        return {}
    contas = (
        await session.execute(
            select(FlexConta).where(FlexConta.integration_id.in_(list(permitidas)))
        )
    ).scalars()
    local = flex_local.atual()
    out: dict[UUID, Integration] = {}
    for c in contas:
        if c.flex_ativo is not True:
            continue
        if c.plataforma == "shopee" and not flex_config.shopee_escrita():
            continue
        if c.plataforma == "ml" and not flex_local.origem_ok(c.origem_cidade, local):
            continue
        out[c.integration_id] = permitidas[c.integration_id]
    return out


async def _titulos(session: AsyncSession, chaves: list[tuple[UUID, str]]) -> dict:
    """(conta, anúncio) → título: o da lista importada; senão o do vínculo."""
    out: dict[tuple[UUID, str], str] = {}
    if not chaves:
        return out
    ids = list({iid for iid, _ in chaves})
    exts = list({ext for _, ext in chaves})
    for iid, ext, titulo in (
        await session.execute(
            select(Listing.integration_id, Listing.external_id, Listing.title).where(
                Listing.integration_id.in_(ids),
                or_(
                    Listing.external_id.in_(exts),
                    func.split_part(Listing.external_id, "_", 1).in_(exts),
                ),
            )
        )
    ).all():
        if titulo:
            out.setdefault((iid, str(ext).split("_")[0]), titulo)
    for iid, ext, titulo in (
        await session.execute(
            select(
                ProductLink.integration_id, ProductLink.external_id, ProductLink.listing_title
            ).where(ProductLink.integration_id.in_(ids), ProductLink.external_id.in_(exts))
        )
    ).all():
        if titulo:
            out.setdefault((iid, ext), titulo)
    return out


async def vigia_flex_run(session: AsyncSession, *, agora: datetime | None = None) -> dict:
    agora = agora or datetime.now(UTC)
    await flex_local.carregar(forcar=True)  # relê também as Chaves do Flex da tela
    async with ouvidoria.Rodada(session, ROBO) as r:
        for k in ("contas", "presos", "promocao", "gtin", "outro", "listas_paradas"):
            r.contadores[k] = 0
        for k in ("novas", "persistem"):
            r.contadores[k] = 0
        robo = await session.get(OuvidoriaRobo, ROBO)
        cfg = ouvidoria.config_do_robo(robo, ROBO)
        minimas = int(cfg.get("tentativas_minimas") or _TENTATIVAS_MINIMAS)
        horas = int(cfg.get("horas_importacao") or _HORAS_IMPORTACAO)

        async def registrar(chave: str, **campos: Any) -> None:
            row = await r.registrar(
                chave=chave,
                link=LINK,
                severidade="pessoa",
                precisa_pessoa=True,
                agora=agora,
                **campos,
            )
            if row.fechada_em is None:
                r.contadores["novas" if row.aberta_em == agora else "persistem"] += 1

        modo = flex_config.modo()
        if not flex_config.pode_escrever(modo):
            # Em "observar"/"desligado" o robô do Flex não desliga nada: não há
            # preso — é o modo. Fecha o que estava aberto.
            r.contadores["sumiram"] = await r.fechar_nao_vistas()
            r.resumo = f"Flex em modo {modo}: o sistema não desliga nada — nada a vigiar"
            return {**dict(r.contadores), "avisadas": 0, "resumo": r.resumo}

        prontas = await _contas_prontas(session)
        r.contadores["contas"] = len(prontas)

        # 1) anúncio ligado que a regra quer desligado e o robô não conseguiu
        presos = []
        if prontas:
            presos = (
                (
                    await session.execute(
                        select(FlexAnuncioEstado).where(
                            FlexAnuncioEstado.integration_id.in_(list(prontas)),
                            FlexAnuncioEstado.observado == flex_motor.LIGADO,
                            FlexAnuncioEstado.desejado != flex_motor.LIGADO,
                            FlexAnuncioEstado.ultimo_erro.is_not(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
        titulos = await _titulos(session, [(e.integration_id, e.external_id) for e in presos])
        cidade = flex_local.atual().cidade
        for e in presos:
            tipo = tipo_do_erro(e.ultimo_erro)
            tentativas = int(e.tentativas or 0)
            if tipo == TIPO_OUTRO and tentativas < minimas:
                continue  # erro passageiro: a próxima rodada tenta de novo
            integ = prontas[e.integration_id]
            conta = (integ.name or "").strip()
            plat = "Shopee" if e.plataforma == "shopee" else "Mercado Livre"
            canal = "Entrega Direta" if e.plataforma == "shopee" else "Flex"
            titulo = titulos.get((e.integration_id, e.external_id)) or f"anúncio {e.external_id}"
            erro = _erro_limpo(e.ultimo_erro)
            porque = {
                TIPO_PROMOCAO: f"a {plat} não deixa desligar (promoção)",
                TIPO_GTIN: f"a {plat} não deixa desligar (falta GTIN)",
                TIPO_OUTRO: f"a {plat} recusou desligar",
            }[tipo]
            r.contadores["presos"] += 1
            r.contadores[tipo] += 1
            await registrar(
                f"anuncio:{e.integration_id}:{e.external_id}",
                titulo=f"{conta}: {titulo[:70]} — {canal} ligado sem peça e {porque}"[:200],
                plataforma=e.plataforma,
                conta=conta,
                detalhe=(
                    f"Anúncio {e.external_id} ({conta}, {plat}) está com {canal} LIGADO, mas o "
                    f"sistema quer desligado: {flex_textos.motivo_claro(e.motivo) or e.motivo}. "
                    f"Peças livres em {cidade}: "
                    f"{e.saldo_sp if e.saldo_sp is not None else 'sem estoque conhecido'}. "
                    f"O robô do Flex tentou desligar {tentativas} vez(es) e a {plat} recusou: "
                    f"{erro}. Próxima tentativa: {_hora(e.proxima_tentativa)}. Enquanto ficar "
                    f"ligado, o comprador pode comprar com {canal} um produto sem peça em "
                    f"{cidade}."
                ),
                acao=_acao(tipo, e.plataforma, e.external_id, erro),
                dados={
                    "integration_id": str(e.integration_id),
                    "external_id": e.external_id,
                    "tipo": tipo,
                    "tentativas": tentativas,
                },
            )

        # 2) a lista de anúncios da Shopee parada (o robô do Flex fica cego
        #    para anúncio novo)
        shopee = [iid for iid, i in prontas.items() if flex_motor._plataforma_de(i) == "shopee"]  # noqa: SLF001
        if shopee:
            ultimas = dict(
                (
                    await session.execute(
                        select(Listing.integration_id, func.max(Listing.imported_at))
                        .where(Listing.integration_id.in_(shopee))
                        .group_by(Listing.integration_id)
                    )
                ).all()
            )
            limite = agora - timedelta(hours=horas)
            for iid in shopee:
                ultima = ultimas.get(iid)
                if ultima is not None and ultima >= limite:
                    continue
                conta = (prontas[iid].name or "").strip()
                r.contadores["listas_paradas"] += 1
                await registrar(
                    f"lista:{iid}",
                    titulo=(
                        f"{conta}: a lista de anúncios da Shopee não atualiza desde {_hora(ultima)}"
                    )[:200],
                    plataforma="shopee",
                    conta=conta,
                    detalhe=(
                        f"A importação de 1h30 não trouxe os anúncios da {conta} nas últimas "
                        f"{horas} h (última: {_hora(ultima)}). Anúncio criado depois disso "
                        "fica invisível ao robô do Flex: se nascer com Entrega Direta ligada, "
                        "ninguém desliga."
                    ),
                    acao=(
                        "Ver em Integrações se a conta Shopee está conectada (botão testar); "
                        "se estiver, chamar o suporte do DaVinci (log shopee_anuncios_import)."
                    ),
                    dados={"integration_id": str(iid)},
                )

        r.contadores["sumiram"] = await r.fechar_nao_vistas()
        n = r.contadores["presos"]
        partes = [
            f"{r.contadores[k]} {rot}"
            for k, rot in ((TIPO_PROMOCAO, "promoção"), (TIPO_GTIN, "GTIN"), (TIPO_OUTRO, "outro"))
            if r.contadores[k]
        ]
        r.resumo = (
            f"{n} anúncio(s) com Flex ligado sem peça que o sistema não consegue desligar"
            f" ({', '.join(partes)})"
            if n
            else f"nenhum anúncio preso · {r.contadores['contas']} conta(s) conferida(s)"
        )
        if r.contadores["listas_paradas"]:
            r.resumo += f" · {r.contadores['listas_paradas']} lista(s) da Shopee parada(s)"
    aviso = await ouvidoria.avisar_pendentes(session, ROBO, agora=agora)
    return {**dict(r.contadores), "avisadas": aviso.get("avisadas", 0), "resumo": r.resumo}


async def vigia_flex_sweep() -> dict:
    """Tick do worker (:24/:54) e "Rodar agora": sessão própria, uma rodada por vez."""
    async with session_scope() as trava:
        got = (
            await trava.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": SYNC_NAMESPACE, "key": _SWEEP_LOCK_KEY},
            )
        ).scalar()
        if not got:
            return {"skipped": "lock_busy"}
        async with session_scope() as session:
            return await vigia_flex_run(session)
