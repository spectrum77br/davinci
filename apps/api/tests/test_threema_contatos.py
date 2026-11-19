"""Contatos externos do Threema em `threema_contato` (tarefa 16, C4).

* a migration 0506 COPIA a linha antiga (`threema_informar_config`,
  contexto `diretorio`) para `threema_contato` (origem `migracao`), mantém a
  linha, não duplica ao rodar de novo e desce apagando só o que copiou;
* o seletor (Ouvidoria › Robôs e Informar) mostra EXATAMENTE o mesmo de
  antes — comparado com a implementação antiga congelada aqui;
* salvar e remover gravam na tabela nova e reescrevem a linha antiga como
  espelho (reverter o código não perde ninguém);
* contato que um envio ou grupo usa não é apagado: fica inativo.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.models import (
    Base,
    ThreemaContato,
    ThreemaGrupo,
    ThreemaGrupoMembro,
    ThreemaInformarConfig,
    User,
    UserRole,
    UserStatus,
)
from app.services import threema

_API = Path(__file__).resolve().parent.parent
_MIGRATION = _API / "alembic" / "versions" / "0506_threema_contatos_copia.py"

# A linha antiga como estava em produção (Roma) mais os casos de borda.
LINHA_ANTIGA = (
    "TSTR0001:Roma,tstb0002:Bruno Contato;TSTU0003:Apelido do usuário,"
    "TSTE0004:Apelido do contato,TSTR0001:Roma repetido,TSTV0005:,semdoispontos,"
    "TSTZ0006:Zeca"
)
ENV = SimpleNamespace(
    threema_recipient_names="TSTE0004:Nome do env,TSTN0007:Só no env",
    threema_recipients="TSTN0007,TSTN0008",
)


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0506", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rodar_migration(passo: str):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mod = _carregar()
    mod.SCHEMA = Base.metadata.schema

    def _run(conn) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, passo)()

    return _run


async def _migrar(db, passo: str = "upgrade") -> None:
    conn = await db.connection()
    await conn.run_sync(_rodar_migration(passo))
    await db.commit()


async def _diretorio_antigo(session) -> list[dict[str, str]]:
    """O `threema.diretorio` de ANTES da C4, congelado: os contatos vinham da
    linha `diretorio` de `threema_informar_config`."""
    from sqlalchemy import func

    rows = (
        (
            await session.execute(
                select(User).where(
                    User.threema.is_not(None),
                    func.trim(User.threema) != "",
                    User.status == UserStatus.ACTIVE,
                    User.disabled_at.is_(None),
                    User.open_id.notlike("system:%"),
                )
            )
        )
        .scalars()
        .all()
    )
    por_id: dict[str, str] = {}
    for u in rows:
        for rid in threema.parse_recipients(u.threema):
            por_id.setdefault(rid, u.name or u.email)
    s = threema.get_settings()
    linha = (
        await session.execute(
            select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == "diretorio")
        )
    ).scalar_one_or_none()
    avulsos = threema.parse_recipient_directory(linha.recipients if linha else "", None)
    env = threema.parse_recipient_directory(s.threema_recipient_names, s.threema_recipients)
    out = [{"id": rid, "nome": nome, "origem": "usuario"} for rid, nome in por_id.items()]
    vistos = set(por_id)
    for d in avulsos:
        if d["id"] not in vistos:
            out.append({**d, "origem": "contato"})
            vistos.add(d["id"])
    for d in env:
        if d["id"] not in vistos:
            out.append({**d, "origem": "env"})
            vistos.add(d["id"])
    out.sort(key=lambda d: (d["nome"] or "").lower())
    return out


async def _usuario(db, *, nome, threema_id, status=UserStatus.ACTIVE, open_id=None) -> User:
    email = f"{nome.lower().replace(' ', '.')}@davinci-test.com"
    u = User(
        open_id=open_id or f"email:{email}",
        email=email,
        name=nome,
        role=UserRole.USER,
        status=status,
        threema=threema_id,
    )
    db.add(u)
    await db.commit()
    return u


async def _cenario(db) -> None:
    await _usuario(db, nome="Usuario Ativo", threema_id="tstu0003")
    await _usuario(db, nome="Suspenso", threema_id="TSTZ0006", status=UserStatus.SUSPENDED)
    await _usuario(db, nome="Robo", threema_id="TSTS0009", open_id="system:robo")
    db.add(ThreemaInformarConfig(contexto="diretorio", recipients=LINHA_ANTIGA))
    await db.commit()


async def _contatos_no_banco(db) -> list[tuple]:
    await db.commit()
    return [
        (c.threema_id, c.nome, c.ativo, c.origem)
        for c in (
            await db.execute(select(ThreemaContato).order_by(ThreemaContato.threema_id))
        ).scalars()
    ]


async def _linha(db) -> str | None:
    await db.commit()
    return (
        await db.execute(
            text("SELECT recipients FROM threema_informar_config WHERE contexto = 'diretorio'")
        )
    ).scalar_one_or_none()


# ─────────────────────────────────────────────────────────── migration 0506


def test_0506_le_a_linha_como_o_parse_de_sempre():
    mod = _carregar()
    assert mod.revision == "0506_threema_contatos_copia"
    assert mod.down_revision == "0505_threema_cadastro"
    assert mod.THREEMA_ID_REGEX == threema._THREEMA_ID.pattern
    antigo = threema.parse_recipient_directory(LINHA_ANTIGA, None)
    # mesma leitura do parse (ID em maiúsculas, nome vazio vira o ID, 1º vence),
    # sem o pedaço que não tem ':'
    assert mod.contatos_da_linha(LINHA_ANTIGA) == [(d["id"], d["nome"]) for d in antigo]
    assert ("TSTV0005", "TSTV0005") in mod.contatos_da_linha(LINHA_ANTIGA)
    # ID fora do formato não entra; nome cortado em 80
    assert mod.contatos_da_linha("roma:Roma,TSTL0001:" + "x" * 90) == [("TSTL0001", "x" * 80)]
    assert mod.contatos_da_linha(None) == []


@pytest.mark.asyncio
async def test_0506_copia_mantem_a_linha_nao_duplica_e_desce(db):
    await _cenario(db)
    await _migrar(db)
    copiados = await _contatos_no_banco(db)
    assert copiados == [
        ("TSTB0002", "Bruno Contato", True, "migracao"),
        ("TSTE0004", "Apelido do contato", True, "migracao"),
        ("TSTR0001", "Roma", True, "migracao"),
        ("TSTU0003", "Apelido do usuário", True, "migracao"),
        ("TSTV0005", "TSTV0005", True, "migracao"),
        ("TSTZ0006", "Zeca", True, "migracao"),
    ]
    assert await _linha(db) == LINHA_ANTIGA  # a linha antiga fica como estava

    await _migrar(db)  # de novo: o ID é único, nada duplica
    assert await _contatos_no_banco(db) == copiados

    db.add(ThreemaContato(threema_id="TSTT0010", nome="Da tela", origem="tela"))
    await db.commit()
    await _migrar(db, "downgrade")
    assert await _contatos_no_banco(db) == [("TSTT0010", "Da tela", True, "tela")]
    assert await _linha(db) == LINHA_ANTIGA


@pytest.mark.asyncio
async def test_0506_sem_linha_antiga_nao_copia_nada(db):
    await _migrar(db)
    assert await _contatos_no_banco(db) == []


# ─────────────────────────────────────────────────────────── o seletor


@pytest.mark.asyncio
async def test_seletor_da_ouvidoria_mostra_exatamente_o_mesmo(
    db, monkeypatch, client, make_user, auth_as
):
    monkeypatch.setattr(threema, "get_settings", lambda: ENV)
    await _cenario(db)
    antes = await _diretorio_antigo(db)
    # o cenário cobre: usuário vence contato e env no nome, contato vence env,
    # suspenso e usuário-sistema ficam de fora (e o contato com o ID do
    # suspenso aparece), ID só no env
    por_id = {d["id"]: d for d in antes}
    assert por_id["TSTU0003"] == {"id": "TSTU0003", "nome": "Usuario Ativo", "origem": "usuario"}
    assert por_id["TSTE0004"]["origem"] == "contato"
    assert por_id["TSTZ0006"] == {"id": "TSTZ0006", "nome": "Zeca", "origem": "contato"}
    assert por_id["TSTN0008"]["origem"] == "env"
    assert "TSTS0009" not in por_id

    await _migrar(db)
    # o banco não sabe mais da linha antiga: apagar a linha não muda o seletor
    await db.execute(text("DELETE FROM threema_informar_config WHERE contexto = 'diretorio'"))
    await db.commit()
    assert await threema.diretorio(db) == antes

    leitor = await make_user(
        email="leitor@davinci-test.com",
        permissions={"ouvidoria": {"view": True, "edit": False, "delete": False}},
    )
    auth_as(leitor)
    r = await client.get("/api/ouvidoria/threema/destinatarios")
    assert r.status_code == 200, r.text
    assert r.json() == antes


@pytest.mark.asyncio
async def test_salvar_e_remover_gravam_na_tabela_nova_e_espelham_a_linha(db, monkeypatch):
    monkeypatch.setattr(threema, "get_settings", lambda: ENV)
    await _cenario(db)
    await _migrar(db)

    await threema.salvar_contato(db, threema_id="tstn0011", nome="  Novo   Contato ")
    await threema.salvar_contato(db, threema_id="TSTR0001", nome="Roma Renomeado")
    await db.commit()
    contatos = {c[0]: c for c in await _contatos_no_banco(db)}
    assert contatos["TSTN0011"] == ("TSTN0011", "Novo Contato", True, "tela")
    assert contatos["TSTR0001"][1:] == ("Roma Renomeado", True, "migracao")
    # o espelho é lido pela implementação antiga igual ao seletor de hoje
    assert await _diretorio_antigo(db) == await threema.diretorio(db)
    assert "TSTN0011:Novo Contato" in (await _linha(db)).split(",")

    await threema.remover_contato(db, "tstn0011")
    await threema.remover_contato(db, "TSTX9999")  # quem não existe: nada
    await db.commit()
    assert "TSTN0011" not in {c[0] for c in await _contatos_no_banco(db)}
    assert await _diretorio_antigo(db) == await threema.diretorio(db)
    assert all(d["id"] != "TSTN0011" for d in await threema.diretorio(db))


@pytest.mark.asyncio
async def test_espelho_nasce_quando_nao_havia_linha(db, monkeypatch):
    monkeypatch.setattr(threema, "get_settings", lambda: ENV)
    await threema.salvar_contato(db, threema_id="TSTR0001", nome="Roma")
    await db.commit()
    assert await _linha(db) == "TSTR0001:Roma"
    assert await threema.contatos(db) == [{"id": "TSTR0001", "nome": "Roma"}]


@pytest.mark.asyncio
async def test_contato_em_uso_no_cadastro_fica_inativo_em_vez_de_sumir(db, monkeypatch):
    monkeypatch.setattr(threema, "get_settings", lambda: ENV)
    await threema.salvar_contato(db, threema_id="TSTR0001", nome="Roma")
    await db.commit()
    contato = (await db.execute(select(ThreemaContato))).scalar_one()
    grupo = ThreemaGrupo(nome="Avisos gerais")
    db.add(grupo)
    await db.flush()
    db.add(ThreemaGrupoMembro(grupo_id=grupo.id, tipo="contato", contato_id=contato.id))
    await db.commit()

    await threema.remover_contato(db, "TSTR0001")
    await db.commit()
    assert await _contatos_no_banco(db) == [("TSTR0001", "Roma", False, "tela")]
    assert await threema.contatos(db) == []
    assert all(d["id"] != "TSTR0001" for d in await threema.diretorio(db))
    assert await _linha(db) == ""
    # cadastrar de novo reativa
    await threema.salvar_contato(db, threema_id="TSTR0001", nome="Roma")
    await db.commit()
    assert await _contatos_no_banco(db) == [("TSTR0001", "Roma", True, "tela")]


@pytest.mark.asyncio
async def test_validacoes_de_sempre_mais_o_tamanho_do_nome(db):
    with pytest.raises(threema.ContatoInvalidoError, match="8 letras"):
        await threema.salvar_contato(db, threema_id="roma", nome="Roma")
    with pytest.raises(threema.ContatoInvalidoError, match="Dê um nome"):
        await threema.salvar_contato(db, threema_id="TSTR0001", nome="   ")
    with pytest.raises(threema.ContatoInvalidoError, match="não pode ter"):
        await threema.salvar_contato(db, threema_id="TSTR0001", nome="Roma: chefe")
    with pytest.raises(threema.ContatoInvalidoError, match="até 80"):
        await threema.salvar_contato(db, threema_id="TSTR0001", nome="x" * 81)
    assert threema.threema_id_valido(" tstr0001 ")
    assert threema.threema_id_valido("*TSTGW01")
    assert not threema.threema_id_valido("TSTR001")
