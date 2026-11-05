"""303842 (05/11/2026): a TikTok devolve link novo das fotos e vídeos a cada leitura e
o cron gravava a mesma fala de novo. A fala é a mesma com outro link; a migration 0452
limpa o que já tinha sido gravado."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.models import Chamado, ChamadoMensagem
from app.services import chamados_devolucao as cd
from app.services import chamados_devolucao_sync as sync

pytestmark = pytest.mark.asyncio

QUANDO = datetime(2026, 10, 17, 12, 0, 48, tzinfo=UTC)
FALA = (
    "Comprador 17/10 09:00: Qualidade baixissima — Anexou 2 vídeos: "
    "https://v16.tiktokcdn.com/{}/video | https://v16.tiktokcdn.com/{}/b"
)


async def _chamado(db) -> Chamado:
    ch = Chamado(id=uuid4(), origem="devolucao", canal="api", plataforma="tiktok")
    db.add(ch)
    await db.commit()
    return ch


async def _falas(db, ch) -> list[ChamadoMensagem]:
    return list(
        (await db.execute(select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch.id)))
        .scalars()
        .all()
    )


async def test_mesma_fala_com_link_novo_nao_duplica_e_atualiza_o_link(db):
    ch = await _chamado(db)
    assert await sync.registrar_recebida(
        db, ch, cd.PLAT_TIKTOK, FALA.format("a1", "b1"), quando=QUANDO
    )
    await db.commit()
    novo = FALA.format("a2", "b2")
    assert not await sync.registrar_recebida(db, ch, cd.PLAT_TIKTOK, novo, quando=QUANDO)
    await db.commit()
    falas = await _falas(db, ch)
    assert [m.texto for m in falas] == [novo]
    assert falas[0].created_at == QUANDO


async def test_texto_diferente_alem_do_link_e_fala_nova(db):
    ch = await _chamado(db)
    await sync.registrar_recebida(db, ch, cd.PLAT_TIKTOK, FALA.format("a1", "b1"))
    await db.commit()
    outra = FALA.format("a1", "b1").replace("09:00", "11:03")
    assert await sync.registrar_recebida(db, ch, cd.PLAT_TIKTOK, outra)
    await db.commit()
    assert len(await _falas(db, ch)) == 2


async def test_fala_sem_link_continua_igual(db):
    ch = await _chamado(db)
    assert await sync.registrar_recebida(db, ch, cd.PLAT_TIKTOK, "Comprador enviou o pacote")
    await db.commit()
    assert not await sync.registrar_recebida(db, ch, cd.PLAT_TIKTOK, "Comprador enviou o pacote")
    await db.commit()
    assert len(await _falas(db, ch)) == 1


async def test_migration_deixa_so_a_copia_mais_nova(db):
    _migration = (
        Path(__file__).parents[1] / "alembic/versions/0452_chamados_dias_e_tiktok_repetidas.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0452", _migration)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    schema = (await db.execute(text("select current_schema()"))).scalar_one()

    ch = await _chamado(db)

    def fala(
        link: str, *, autor="TikTok Shop", quando=QUANDO, idade=0, texto=None
    ) -> ChamadoMensagem:
        return ChamadoMensagem(
            chamado_id=ch.id,
            direcao="recebida",
            tipo="resposta",
            texto=texto or FALA.format(link, link),
            canal="api",
            status="registrada",
            autor_nome=autor,
            created_at=quando,
            updated_at=datetime(2026, 11, 5, tzinfo=UTC) - timedelta(days=idade),
        )

    db.add_all(
        [
            fala("velho", idade=3),
            fala("meio", idade=2),
            fala("novo", idade=1),
            # Outra hora, outro texto ou outra plataforma: não são cópias.
            fala("x", quando=QUANDO + timedelta(days=4)),
            fala("y", texto="Comprador 18/10: sem link nenhum"),
            fala("z", autor="Shopee", idade=5),
            fala("z", autor="Shopee", idade=4),
        ]
    )
    await db.commit()

    conn = await db.connection()
    apagadas = await conn.run_sync(lambda c: mig._limpar_repetidas(c, schema=schema))
    await db.commit()
    assert apagadas == 2  # "velho" e "meio"; "x" é a mesma fala em outra hora
    textos = [m.texto for m in await _falas(db, ch)]
    assert FALA.format("novo", "novo") in textos
    assert FALA.format("velho", "velho") not in textos
    assert FALA.format("meio", "meio") not in textos
    assert FALA.format("x", "x") in textos
    assert len(textos) == 5
