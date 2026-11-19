"""Cadastro dos envios do Threema — tabelas e sincronização (tarefa 16, C3).

* a migration 0505 cria as 6 tabelas iguais aos modelos, com os CHECKs e os
  índices únicos, insere a linha 1 da emergência, desce e sobe;
* a sincronização do catálogo faz upsert só das colunas do código, não semeia
  (nada de quem recebe, `semeado_em` NULL), não mexe no que é da pessoa, não
  reescreve linha que não mudou, carimba a chave que saiu do código e roda no
  startup do worker;
* as tabelas do Cadastro ficam com o gatilho do Histórico e com rótulo.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text, update

from app.historico import nomes as historico_nomes
from app.historico import sql as historico_sql
from app.models import (
    Base,
    LogisticaStatus,
    ThreemaCadastroEstado,
    ThreemaContato,
    ThreemaEnvio,
    ThreemaEnvioDestinatario,
    ThreemaGrupo,
    ThreemaGrupoMembro,
)
from app.models import threema_envio as modelos
from app.services import threema_envios
from app.services.threema_catalogo import ENVIOS, K

_API = Path(__file__).resolve().parent.parent
_MIGRATION = _API / "alembic" / "versions" / "0505_threema_cadastro.py"
TABELAS = (
    "threema_envio",
    "threema_contato",
    "threema_grupo",
    "threema_grupo_membro",
    "threema_envio_destinatario",
    "threema_cadastro_estado",
)


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0505", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db, schema: str, tabela: str) -> dict:
    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "") if s else s

    colunas = (
        await db.execute(
            text(
                "SELECT column_name, udt_name, character_maximum_length, is_nullable, "
                "column_default, is_identity "
                "FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t ORDER BY column_name"
            ),
            {"s": schema, "t": tabela},
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                "SELECT co.conname, co.contype::text, pg_get_constraintdef(co.oid) "
                "FROM pg_constraint co JOIN pg_class cl ON cl.oid = co.conrelid "
                "JOIN pg_namespace n ON n.oid = cl.relnamespace "
                "WHERE n.nspname = :s AND cl.relname = :t ORDER BY co.conname"
            ),
            {"s": schema, "t": tabela},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = :s AND tablename = :t ORDER BY indexname"
            ),
            {"s": schema, "t": tabela},
        )
    ).all()
    return {
        "colunas": [tuple(r[:4]) + (limpo(r[4]), r[5]) for r in colunas],
        "constraints": [(r[0], r[1], limpo(r[2])) for r in constraints],
        "indices": [(r[0], limpo(r[1])) for r in indices],
    }


# ─────────────────────────────────────────────────────────── migration 0505


@pytest.mark.asyncio
async def test_0505_cria_igual_aos_modelos_desce_e_sobe(db):
    mod = _carregar()
    assert mod.revision == "0505_threema_cadastro"
    assert mod.down_revision == "0504_threema_envio_registro"
    assert mod.FREQUENCIAS == modelos.FREQUENCIAS_ENVIO
    assert mod.ONDE_RODA == modelos.ONDE_RODA_ENVIO
    assert mod.ORIGENS_CONTATO == modelos.ORIGENS_CONTATO
    assert mod.TIPOS_MEMBRO == modelos.TIPOS_MEMBRO
    assert mod.TIPOS_DESTINATARIO == modelos.TIPOS_DESTINATARIO
    assert mod.THREEMA_ID_REGEX == modelos.THREEMA_ID_REGEX
    assert mod.SEM_REGRA == modelos.SEM_REGRA

    modelo = Base.metadata.schema
    rascunho = f"{modelo}_m0505"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    await db.execute(text(f'CREATE TABLE "{rascunho}".users (id uuid PRIMARY KEY)'))
    await db.execute(text(f'CREATE TABLE "{rascunho}".logistica_status (id uuid PRIMARY KEY)'))
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, passo)()

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        for tabela in TABELAS:
            da_migration = await _catalogo(db, rascunho, tabela)
            assert da_migration["colunas"], f"a migration não criou {tabela}"
            assert da_migration == await _catalogo(db, modelo, tabela), tabela

        # a linha da emergência nasce desligada; só cabe a linha 1
        linhas = (
            await db.execute(
                text(f'SELECT id, usar_legado FROM "{rascunho}".threema_cadastro_estado')
            )
        ).all()
        assert [tuple(r) for r in linhas] == [(1, False)]
        with pytest.raises(Exception):  # noqa: B017,PT011 — CHECK do banco
            await db.execute(
                text(f'INSERT INTO "{rascunho}".threema_cadastro_estado (id) VALUES (2)')
            )
        await db.rollback()
        # sem INSERT em threema_envio: quem cria é a sincronização
        assert (
            await db.execute(text(f'SELECT count(*) FROM "{rascunho}".threema_envio'))
        ).scalar_one() == 0

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        for tabela in TABELAS:
            assert (await _catalogo(db, rascunho, tabela))["colunas"] == [], tabela
        # as tabelas de antes continuam lá
        assert (await _catalogo(db, rascunho, "users"))["colunas"]

        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        for tabela in TABELAS:
            assert (await _catalogo(db, rascunho, tabela)) == await _catalogo(db, modelo, tabela)
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()


@pytest.mark.asyncio
async def test_os_checks_do_cadastro_valem(db, make_user):
    u = await make_user(email="ck@davinci-test.com")
    await threema_envios.sincronizar(db)
    contato = ThreemaContato(nome="Roma", threema_id="TSTC0001", origem="tela")
    grupo = ThreemaGrupo(nome="Avisos gerais")
    db.add_all([contato, grupo])
    await db.commit()
    # rollback expira os objetos: guardar os ids antes dos erros esperados
    uid, cid, gid = u.id, contato.id, grupo.id

    # ID fora do formato, origem desconhecida
    for kw in ({"threema_id": "roma"}, {"threema_id": "TSTC0002", "origem": "robo"}):
        db.add(ThreemaContato(**{"nome": "X", "origem": "tela", **kw}))
        with pytest.raises(Exception):  # noqa: B017,PT011
            await db.commit()
        await db.rollback()
    # mesmo ID duas vezes
    db.add(ThreemaContato(nome="Outro", threema_id="TSTC0001", origem="tela"))
    with pytest.raises(Exception):  # noqa: B017,PT011
        await db.commit()
    await db.rollback()
    # nome de grupo único sem diferenciar maiúsculas
    db.add(ThreemaGrupo(nome="AVISOS GERAIS"))
    with pytest.raises(Exception):  # noqa: B017,PT011
        await db.commit()
    await db.rollback()

    # membro: exatamente uma referência, coerente com o tipo
    db.add(ThreemaGrupoMembro(grupo_id=gid, tipo="usuario", user_id=uid))
    await db.commit()
    for kw in (
        {"tipo": "usuario", "contato_id": cid},
        {"tipo": "contato", "user_id": uid, "contato_id": cid},
        {"tipo": "usuario", "user_id": uid},  # repetido no grupo
    ):
        db.add(ThreemaGrupoMembro(grupo_id=gid, **kw))
        with pytest.raises(Exception):  # noqa: B017,PT011
            await db.commit()
        await db.rollback()

    # destinatário: uma referência, coerente; único por envio + regra
    regra = LogisticaStatus(status_plataforma="Teste C3")
    db.add(regra)
    await db.commit()
    rid = regra.id
    chave = K.LOGISTICA_MENSAGEM_REGRA
    db.add_all(
        [
            ThreemaEnvioDestinatario(envio_chave=chave, tipo="grupo", grupo_id=gid),
            ThreemaEnvioDestinatario(
                envio_chave=chave, logistica_status_id=rid, tipo="grupo", grupo_id=gid
            ),
        ]
    )
    await db.commit()
    for kw in (
        {"tipo": "grupo", "grupo_id": gid},  # repetido na lista padrão
        {"tipo": "usuario", "grupo_id": gid},
        {"tipo": "contato", "contato_id": cid, "user_id": uid},
    ):
        db.add(ThreemaEnvioDestinatario(envio_chave=chave, **kw))
        with pytest.raises(Exception):  # noqa: B017,PT011
            await db.commit()
        await db.rollback()
    # chave que não existe em threema_envio
    db.add(ThreemaEnvioDestinatario(envio_chave="x.nao_existe", tipo="grupo", grupo_id=gid))
    with pytest.raises(Exception):  # noqa: B017,PT011
        await db.commit()
    await db.rollback()

    # grupo em uso não some calado (RESTRICT); contato e usuário saem em cascata
    with pytest.raises(Exception):  # noqa: B017,PT011
        await db.execute(text("DELETE FROM threema_grupo WHERE id = :g"), {"g": gid})
    await db.rollback()
    await db.execute(text("DELETE FROM logistica_status WHERE id = :r"), {"r": rid})
    await db.commit()
    restantes = (
        await db.execute(
            select(func.count())
            .select_from(ThreemaEnvioDestinatario)
            .where(ThreemaEnvioDestinatario.envio_chave == chave)
        )
    ).scalar_one()
    assert restantes == 1  # a da regra apagada foi junto


# ─────────────────────────────────────────────────────────── sincronização


async def _envios(db) -> dict[str, ThreemaEnvio]:
    await db.commit()
    db.expire_all()
    return {r.chave: r for r in (await db.execute(select(ThreemaEnvio))).scalars().all()}


@pytest.mark.asyncio
async def test_sincronizar_cria_as_60_linhas_sem_semear(db):
    resumo = await threema_envios.sincronizar(db)
    assert resumo == {"novos": len(ENVIOS), "atualizados": 0, "descontinuados": 0}
    linhas = await _envios(db)
    assert len(linhas) == len(ENVIOS) == 60
    for d in ENVIOS:
        r = linhas[d.chave]
        assert (r.painel, r.nome, r.descricao) == (d.painel, d.nome, d.descricao)
        assert (r.frequencia_tipo, r.frequencia_texto, r.onde_roda) == (
            d.frequencia_tipo,
            d.frequencia_texto,
            d.onde_roda,
        )
        # nada da pessoa, nada de semente
        assert r.ativo is None and r.observacao is None
        assert r.semeado_em is None and r.descontinuado_em is None
    assert (
        await db.execute(select(func.count()).select_from(ThreemaEnvioDestinatario))
    ).scalar_one() == 0
    assert (await db.execute(select(func.count()).select_from(ThreemaGrupo))).scalar_one() == 0
    assert (await db.execute(select(func.count()).select_from(ThreemaContato))).scalar_one() == 0


@pytest.mark.asyncio
async def test_sincronizar_de_novo_nao_reescreve_nada(db):
    await threema_envios.sincronizar(db)
    await db.commit()
    antes = {c: r.updated_at for c, r in (await _envios(db)).items()}
    assert await threema_envios.sincronizar(db) == {
        "novos": 0,
        "atualizados": 0,
        "descontinuados": 0,
    }
    depois = {c: r.updated_at for c, r in (await _envios(db)).items()}
    assert antes == depois


@pytest.mark.asyncio
async def test_sincronizar_reescreve_so_o_que_e_do_codigo(db):
    await threema_envios.sincronizar(db)
    await db.commit()
    chave = K.LOGISTICA_VIGIA_AUTOMACAO
    await db.execute(
        update(ThreemaEnvio)
        .where(ThreemaEnvio.chave == chave)
        .values(
            painel="Outro",
            nome="nome velho",
            ativo=False,
            ativo_alterado_por="admin@davinci-test.com",
            observacao="desligado para teste",
        )
    )
    await db.commit()
    assert await threema_envios.sincronizar(db) == {
        "novos": 0,
        "atualizados": 1,
        "descontinuados": 0,
    }
    r = (await _envios(db))[chave]
    assert (r.painel, r.nome) == ("Logística", "Automação parada")
    # o que é da pessoa ficou
    assert r.ativo is False
    assert r.ativo_alterado_por == "admin@davinci-test.com"
    assert r.observacao == "desligado para teste"
    assert r.semeado_em is None


@pytest.mark.asyncio
async def test_chave_que_saiu_do_codigo_fica_descontinuada_e_volta(db):
    await threema_envios.sincronizar(db)
    db.add(
        ThreemaEnvio(
            chave="logistica.aviso_antigo",
            painel="Logística",
            nome="Aviso que saiu do código",
            frequencia_tipo="evento",
            onde_roda="servidor",
            observacao="fica",
        )
    )
    await db.commit()
    assert (await threema_envios.sincronizar(db))["descontinuados"] == 1
    antiga = (await _envios(db))["logistica.aviso_antigo"]
    assert antiga.descontinuado_em is not None and antiga.observacao == "fica"
    # rodar de novo não carimba outra vez
    assert (await threema_envios.sincronizar(db))["descontinuados"] == 0

    # chave do código que tinha sido carimbada (voltou ao código) perde o carimbo
    chave = K.MARGEM_INFORMAR
    await db.execute(
        update(ThreemaEnvio).where(ThreemaEnvio.chave == chave).values(descontinuado_em=func.now())
    )
    await db.commit()
    assert (await threema_envios.sincronizar(db))["atualizados"] == 1
    assert (await _envios(db))[chave].descontinuado_em is None


@pytest.mark.asyncio
async def test_startup_do_worker_sincroniza_o_catalogo(db):
    from app import worker

    await worker.startup({})
    linhas = await _envios(db)
    assert len(linhas) == len(ENVIOS)
    assert all(r.semeado_em is None for r in linhas.values())


@pytest.mark.asyncio
async def test_startup_do_worker_sobe_mesmo_com_a_sincronizacao_quebrada(db, monkeypatch):
    from app import worker

    async def _quebra(_session):
        raise RuntimeError("threema_envio não existe")

    monkeypatch.setattr(threema_envios, "sincronizar", _quebra)
    await worker.startup({})  # não levanta


def test_catalogo_cabe_nas_colunas():
    tabela = ThreemaEnvio.__table__
    for d in ENVIOS:
        for coluna in ("chave", "painel", "nome", "frequencia_texto"):
            tamanho = tabela.c[coluna].type.length
            assert len(getattr(d, coluna)) <= tamanho, (d.chave, coluna)
        assert d.frequencia_tipo in modelos.FREQUENCIAS_ENVIO
        assert d.onde_roda in modelos.ONDE_RODA_ENVIO


@pytest.mark.asyncio
async def test_tabelas_do_cadastro_tem_o_gatilho_e_rotulo_do_historico(db):
    from tests.conftest import TEST_SCHEMA

    com = {
        r[0]
        for r in await db.execute(
            text(
                "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = :schema AND t.tgname = :g"
            ),
            {"schema": TEST_SCHEMA, "g": historico_sql.NOME_GATILHO},
        )
    }
    for tabela in TABELAS:
        assert tabela in com, tabela
        assert not historico_sql.EXCLUIDAS.search(tabela), tabela
        assert tabela in historico_nomes.TABELAS, tabela
    # o registro de envios continua fora (é escrito pela máquina)
    assert "threema_envio_registro" not in com


@pytest.mark.asyncio
async def test_linha_da_emergencia_nasce_desligada(db):
    """O banco de teste nasce do create_all (sem a linha 1 que a migration
    insere): quem cria a linha pelo modelo também a vê desligada."""
    assert (await db.get(ThreemaCadastroEstado, 1)) is None
    db.add(ThreemaCadastroEstado(id=1))
    await db.commit()
    assert (await db.get(ThreemaCadastroEstado, 1)).usar_legado is False
    assert uuid.UUID(int=0) == uuid.UUID(modelos.SEM_REGRA)
