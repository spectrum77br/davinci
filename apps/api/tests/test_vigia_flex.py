"""Vigia do Flex (Ouvidoria, 12/11/2026): anúncio com Flex/Entrega Direta ligado sem
peça que o robô do Flex não consegue desligar (promoção, GTIN, erro) e lista de
anúncios da Shopee parada. Ver services/vigia_flex."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.config import get_settings
from app.models import (
    FlexAnuncioEstado,
    FlexConta,
    Integration,
    IntegrationPlatform,
    Listing,
    OuvidoriaOcorrencia,
    UserRole,
)
from app.security.cipher import encrypt_json
from app.services import flex_config, flex_local
from app.services import vigia_flex as vf

pytestmark = pytest.mark.asyncio

AGORA = datetime(2026, 11, 13, 0, 54, tzinfo=UTC)
PROMO = (
    "desligar: 200 em promoção na Shopee: Desconto da loja até 25/12 17:00; "
    "Desconto da loja até 28/12 11:35"
)
GTIN = "desligar: 200 product.error_busi: The GTIN code is required for this category."

_TABELAS = (
    "ouvidoria_ocorrencias",
    "ouvidoria_rodadas",
    "ouvidoria_robos",
    "flex_anuncio_estado",
    "flex_conta",
)


@pytest_asyncio.fixture(autouse=True)
async def _limpa(db, monkeypatch):
    for t in _TABELAS:
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await db.commit()
    cfg = get_settings()
    monkeypatch.setattr(cfg, "flex_modo", "ativo")
    monkeypatch.setattr(cfg, "flex_shopee_escrita", True)
    flex_config.limpar_cache()
    flex_local.limpar_cache()
    yield
    for t in _TABELAS:
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await db.commit()
    flex_config.limpar_cache()
    flex_local.limpar_cache()


async def _conta(db, dono, nome, plataforma=IntegrationPlatform.SHOPEE, *, ativo=True, cidade=None):
    i = Integration(
        user_id=dono.id, platform=plataforma, name=nome, credentials=encrypt_json({"t": 1})
    )
    db.add(i)
    await db.flush()
    db.add(
        FlexConta(
            integration_id=i.id,
            plataforma="shopee" if plataforma == IntegrationPlatform.SHOPEE else "ml",
            flex_ativo=ativo,
            origem_cidade=cidade,
        )
    )
    await db.flush()
    return i


def _anuncio(i, ext, *, desejado="desligado", observado="ligado", erro=None, tentativas=0):
    return FlexAnuncioEstado(
        integration_id=i.id,
        external_id=ext,
        plataforma="shopee" if i.platform == IntegrationPlatform.SHOPEE else "ml",
        desejado=desejado,
        motivo="i232.sp não existe ativo — sem estoque .sp conhecido, nunca liga",
        observado=observado,
        ultimo_erro=erro,
        tentativas=tentativas,
        saldo_sp=0,
    )


def _lista(dono, i, ext, *, quando):
    return Listing(
        user_id=dono.id,
        integration_id=i.id,
        platform=i.platform,
        external_id=ext,
        title=f"Apple Watch SE {ext}",
        imported_at=quando,
    )


async def _abertas(db):
    return {
        o.chave: o
        for o in (
            await db.execute(
                select(OuvidoriaOcorrencia).where(
                    OuvidoriaOcorrencia.robo_chave == vf.ROBO,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                )
            )
        ).scalars()
    }


async def test_avisa_preso_por_promocao_e_gtin_e_ignora_o_resto(db, make_user, monkeypatch):
    dono = await make_user(role=UserRole.ADMIN)
    atlas = await _conta(db, dono, "Atlas")
    fiore = await _conta(db, dono, "Fiore")
    sem_ed = await _conta(db, dono, "vortan", ativo=False)  # loja sem Entrega Direta
    ml_pira = await _conta(
        db, dono, "kfa", IntegrationPlatform.ML, cidade="Piracicaba"
    )  # sai de outra cidade: o robô do Flex não mexe
    monkeypatch.setattr(
        get_settings(), "flex_contas", ",".join(str(i.id) for i in (atlas, fiore, sem_ed, ml_pira))
    )
    db.add_all(
        [
            _anuncio(atlas, "23699437869", desejado="inelegivel", erro=PROMO, tentativas=3),
            _anuncio(fiore, "58266878163", erro=GTIN, tentativas=1),
            # erro passageiro com 1 tentativa: espera a próxima rodada
            _anuncio(fiore, "1", erro="desligar: 503 Service Unavailable", tentativas=1),
            # ligado e a regra quer ligado: certo
            _anuncio(atlas, "2", desejado="ligado", erro=None),
            # ligado errado mas o robô ainda nem tentou (sem erro): não é preso
            _anuncio(atlas, "3", erro=None),
            # contas em que o robô não mexe
            _anuncio(sem_ed, "4", erro=PROMO, tentativas=5),
            _anuncio(ml_pira, "MLB5", erro="desligar: 403 item down", tentativas=5),
        ]
    )
    for i in (atlas, fiore):
        db.add(_lista(dono, i, "23699437869" if i is atlas else "58266878163", quando=AGORA))
    await db.commit()

    out = await vf.vigia_flex_run(db, agora=AGORA)
    abertas = await _abertas(db)
    assert set(abertas) == {
        f"anuncio:{atlas.id}:23699437869",
        f"anuncio:{fiore.id}:58266878163",
    }
    assert (out["presos"], out["promocao"], out["gtin"], out["contas"]) == (2, 1, 1, 2)
    o = abertas[f"anuncio:{atlas.id}:23699437869"]
    assert o.conta == "Atlas" and o.plataforma == "shopee"
    assert "Apple Watch SE" in o.titulo and "promoção" in o.titulo
    assert "Tirar o anúncio da promoção" in o.acao and "Desconto da loja até 25/12" in o.acao
    assert "seller.shopee.com.br/portal/product/23699437869" in o.acao
    assert "Tentou" not in o.detalhe and "tentou desligar 3 vez(es)" in o.detalhe
    assert "GTIN" in abertas[f"anuncio:{fiore.id}:58266878163"].acao

    # A Shopee aceitou desligar (a promoção acabou): a ocorrência fecha sozinha.
    est = await db.get(FlexAnuncioEstado, (atlas.id, "23699437869"))
    est.observado = "desligado"
    await db.commit()
    out = await vf.vigia_flex_run(db, agora=AGORA + timedelta(minutes=30))
    assert set(await _abertas(db)) == {f"anuncio:{fiore.id}:58266878163"}
    assert out["sumiram"] == 1


async def test_lista_da_shopee_parada_vira_aviso(db, make_user, monkeypatch):
    dono = await make_user(role=UserRole.ADMIN)
    atlas = await _conta(db, dono, "Atlas")
    mega = await _conta(db, dono, "mega")
    monkeypatch.setattr(get_settings(), "flex_contas", f"{atlas.id},{mega.id}")
    db.add_all(
        [
            _lista(dono, atlas, "1", quando=AGORA - timedelta(hours=1)),
            _lista(dono, mega, "2", quando=datetime(2026, 5, 15, 2, 48, tzinfo=UTC)),
        ]
    )
    await db.commit()
    out = await vf.vigia_flex_run(db, agora=AGORA)
    abertas = await _abertas(db)
    assert set(abertas) == {f"lista:{mega.id}"}
    assert out["listas_paradas"] == 1
    assert "14/05 23:48" in abertas[f"lista:{mega.id}"].titulo  # 15/05 02:48 UTC


async def test_em_observar_nada_e_preso(db, make_user, monkeypatch):
    """Em "observar" o robô do Flex não desliga nada: não é preso, é o modo."""
    dono = await make_user(role=UserRole.ADMIN)
    atlas = await _conta(db, dono, "Atlas")
    monkeypatch.setattr(get_settings(), "flex_contas", str(atlas.id))
    db.add(_anuncio(atlas, "23699437869", erro=PROMO, tentativas=3))
    db.add(_lista(dono, atlas, "23699437869", quando=AGORA))
    await db.commit()
    await vf.vigia_flex_run(db, agora=AGORA)
    assert len(await _abertas(db)) == 1
    monkeypatch.setattr(get_settings(), "flex_modo", "observar")
    flex_config.limpar_cache()
    out = await vf.vigia_flex_run(db, agora=AGORA + timedelta(minutes=30))
    assert await _abertas(db) == {}
    assert "observar" in out["resumo"]


def test_tipo_do_erro_e_texto_limpo():
    assert vf.tipo_do_erro(PROMO) == vf.TIPO_PROMOCAO
    assert vf.tipo_do_erro(GTIN) == vf.TIPO_GTIN
    assert vf.tipo_do_erro("desligar: 503 x") == vf.TIPO_OUTRO
    assert vf._erro_limpo(PROMO).startswith("em promoção na Shopee: Desconto da loja")  # noqa: SLF001
