"""A 0456 dá ao cairo sa todas as lojas de hoje no atendimento (Cairo, 05/11/2026).

Roda a migration de verdade no schema de teste, com lojas e usuários FABRICADOS
(nunca a produção). O que não pode falhar: as chaves gravadas são exatamente as
que a tela de Usuários lista para marcar, o escopo dele passa a enxergar as
lojas, ninguém mais muda, e sem achar exatamente um não-admin nada é gravado.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import (
    AtendimentoCanal,
    AtendimentoLojaPessoa,
    Base,
    Integration,
    StoreInfo,
    UserRole,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import escopo

pytestmark = pytest.mark.lojas_por_pessoa  # a regra de verdade, sem o legado do conftest

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0456_cairo_atendimento_todas_lojas.py"
)
ARQUIVADA = datetime(2026, 7, 7, tzinfo=UTC)


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0456", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SCHEMA = Base.metadata.schema
    return mod


async def _rodar(db, mod, passo: str) -> None:
    def _executar(conn) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, passo)()

    conn = await db.connection()
    await conn.run_sync(_executar)
    await db.commit()


def _integracao(dono, plataforma: str, nome: str, **campos) -> Integration:
    return Integration(
        user_id=dono.id,
        platform=plataforma,
        name=nome,
        credentials=encrypt_json({"access_token": "t", "refresh_token": "r"}),
        **campos,
    )


async def _semear(db, make_user) -> dict:
    dono = await make_user()
    shopee = _integracao(dono, "shopee", "Barbosa")
    bling = _integracao(dono, "bling", "Bling")
    guardada = _integracao(dono, "tiktok", "Velha", archived_at=ARQUIVADA)
    ficha = StoreInfo(user_id=dono.id, platform="shopee", account_name="Barbosa")
    ml = StoreInfo(user_id=dono.id, platform="Mercado Livre", account_name="Kia")
    interna = StoreInfo(user_id=dono.id, platform="interno", account_name="CD")
    presencial = StoreInfo(user_id=dono.id, platform=" Presencial ", account_name="Balcão")
    ficha_guardada = StoreInfo(
        user_id=dono.id, platform="shopee", account_name="Antiga", archived_at=ARQUIVADA
    )
    robo = AtendimentoCanal(
        plataforma="temu", canal="chat", robo_perfil_id="k1abc", cursor={"robo": {"loja": "Mega"}}
    )
    amazon = AtendimentoCanal(plataforma="amazon", canal="chat", externo_ref="amazon:kia")
    zap = AtendimentoCanal(plataforma="whatsapp", canal="zap", externo_ref="zap:5519")
    site = AtendimentoCanal(plataforma="site", canal="chat", externo_ref="site:charlots")
    db.add_all(
        [
            shopee,
            bling,
            guardada,
            ficha,
            ml,
            interna,
            presencial,
            ficha_guardada,
            robo,
            amazon,
            zap,
            site,
        ]
    )
    await db.commit()
    return {
        "shopee": shopee,
        "bling": bling,
        "guardada": guardada,
        "ficha": ficha,
        "ml": ml,
        "interna": interna,
        "presencial": presencial,
        "ficha_guardada": ficha_guardada,
        "robo": robo,
        "amazon": amazon,
        "zap": zap,
        "site": site,
    }


async def _da_tela(db) -> set[str]:
    return {c for loja in await escopo.lojas_para_vincular(db) for c in loja["chaves"]}


async def test_grava_o_mesmo_que_marcar_todas_na_tela(db, make_user):
    mod = _carregar()
    cairo = await make_user(email=mod.EMAIL)
    lojas = await _semear(db, make_user)

    await _rodar(db, mod, "upgrade")

    gravadas = await escopo.chaves_da_pessoa(db, cairo.id)
    assert gravadas == await _da_tela(db)
    assert escopo.chave(escopo.INTEGRACAO, lojas["shopee"].id) in gravadas
    assert escopo.chave(escopo.FICHA, lojas["ml"].id) in gravadas
    assert escopo.chave(escopo.CANAL, lojas["robo"].id) in gravadas
    assert escopo.chave(escopo.CANAL, lojas["amazon"].id) in gravadas
    assert escopo.chave(escopo.CANAL, lojas["zap"].id) in gravadas
    for fora in (lojas["bling"], lojas["guardada"]):
        assert escopo.chave(escopo.INTEGRACAO, fora.id) not in gravadas
    for fora in (lojas["interna"], lojas["presencial"], lojas["ficha_guardada"]):
        assert escopo.chave(escopo.FICHA, fora.id) not in gravadas
    assert escopo.chave(escopo.CANAL, lojas["site"].id) not in gravadas


async def test_o_escopo_passa_a_ver_as_lojas_sem_virar_admin(db, make_user):
    mod = _carregar()
    cairo = await make_user(email=mod.EMAIL)
    lojas = await _semear(db, make_user)

    antes = await escopo.escopo_do_atendimento(db, cairo)
    assert not antes.integration_ids and not antes.canal_ids

    await _rodar(db, mod, "upgrade")

    await db.refresh(cairo)
    assert cairo.role == UserRole.USER
    depois = await escopo.escopo_do_atendimento(db, cairo)
    assert depois.unrestricted is False
    assert lojas["shopee"].id in depois.integration_ids
    assert lojas["robo"].id in depois.canal_ids
    assert lojas["site"].id not in depois.canal_ids


async def test_mantem_o_que_ele_tinha_e_nao_mexe_nos_outros(db, make_user):
    mod = _carregar()
    cairo = await make_user(email=mod.EMAIL)
    outro = await make_user()
    lojas = await _semear(db, make_user)
    ja_tinha = escopo.chave(escopo.FICHA, lojas["ficha"].id)
    de_outro = escopo.chave(escopo.FICHA, lojas["ml"].id)
    db.add_all(
        [
            AtendimentoLojaPessoa(user_id=cairo.id, chave=ja_tinha, criado_por=outro.id),
            AtendimentoLojaPessoa(user_id=outro.id, chave=de_outro),
        ]
    )
    await db.commit()

    await _rodar(db, mod, "upgrade")
    await _rodar(db, mod, "upgrade")  # rodar de novo não duplica nem quebra

    assert await escopo.chaves_da_pessoa(db, cairo.id) == await _da_tela(db)
    assert await escopo.chaves_da_pessoa(db, outro.id) == {de_outro}


async def test_o_outro_cairo_nao_recebe_nada(db, make_user):
    mod = _carregar()
    outro_cairo = await make_user(email="fredys03@tuta.com")
    await _semear(db, make_user)

    await _rodar(db, mod, "upgrade")

    assert await escopo.chaves_da_pessoa(db, outro_cairo.id) == set()


async def test_admin_nao_ganha_linha(db, make_user):
    mod = _carregar()
    cairo = await make_user(email=mod.EMAIL, role=UserRole.ADMIN)
    await _semear(db, make_user)

    await _rodar(db, mod, "upgrade")

    assert await escopo.chaves_da_pessoa(db, cairo.id) == set()


async def test_downgrade_tira_so_as_linhas_da_migration(db, make_user):
    mod = _carregar()
    cairo = await make_user(email=mod.EMAIL)
    outro = await make_user()
    lojas = await _semear(db, make_user)
    marcada_na_tela = escopo.chave(escopo.FICHA, lojas["ficha"].id)
    db.add(AtendimentoLojaPessoa(user_id=cairo.id, chave=marcada_na_tela, criado_por=outro.id))
    await db.commit()

    await _rodar(db, mod, "upgrade")
    await _rodar(db, mod, "downgrade")

    assert await escopo.chaves_da_pessoa(db, cairo.id) == {marcada_na_tela}
