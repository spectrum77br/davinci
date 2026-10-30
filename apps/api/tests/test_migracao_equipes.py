"""A 0441 põe cada loja e o londres/israel/marrocos na sua equipe (Eduardo, 30/10).

Roda a migration de verdade no schema de teste, com as 79 lojas e os três
usuários FABRICADOS com os mesmos ids da lista (nunca a produção): as lojas vão
para 101/102/103, cada um dos três fica só com a sua equipe, as tags de estoque
e quem está fora da lista não mudam, e o downgrade devolve o retrato de 30/10 só
no que a 0441 mudou e ninguém mexeu depois. Um id que não existe, um usuário que
não é o certo ou uma loja numa terceira equipe param tudo, sem mudar nada; quem
já está no valor novo só é contado.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.models import Base, StoreInfo, User, UserRole, UserStatus

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0441_equipes_londres_israel_marrocos.py"
)
TAGS_LONDRES = ["ci", "pi", "ra", "sa", "cd", "insumos", "eletro", "us", "sp", "mala", "fake"]
TAGS_ISRAEL = ["ci", "pi", "ra", "sa", "sp", "us", "eletro", "fake", "mala"]
TAGS_MARROCOS = ["mala", "sp", "ci", "pi", "us", "eletro", "ra", "cd", "insumos", "sa", "fake"]
# Fora da lista: ficam como estão.
PRESENCIAL, MAKISA, JLAS_ARQUIVADA = str(uuid4()), str(uuid4()), str(uuid4())
ARQUIVADAS = ("ml nexus", "mercadolivre atv")


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0441", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SCHEMA = Base.metadata.schema
    return mod


def _usuario(mod, nome: str) -> UUID:
    return UUID(next(i for i, n, *_ in mod.USUARIOS if n == nome))


def _id_da_loja(mod, conta: str) -> str:
    return next(i for i, c, *_ in mod.LOJAS if c == conta)


async def _semear(db, mod, make_user, *, equipes=None) -> None:
    """As lojas e os usuários como na produção em 30/10.

    `equipes` muda o `sales_team` de uma loja (pelo "plataforma conta")."""
    equipes = equipes or {}
    dono = await make_user()
    for id_, conta, antes, _depois in mod.LOJAS:
        plataforma, nome = conta.split(" ", 1)
        db.add(
            StoreInfo(
                id=UUID(id_),
                user_id=dono.id,
                platform=plataforma,
                account_name=nome,
                sales_team=equipes.get(conta, antes),
                commercial_team=1,
                archived_at=datetime(2026, 7, 7, tzinfo=UTC) if conta in ARQUIVADAS else None,
            )
        )
    db.add_all(
        [
            StoreInfo(
                id=UUID(PRESENCIAL),
                user_id=dono.id,
                platform="presencial",
                account_name="venda presencial",
                sales_team=103,
            ),
            StoreInfo(id=UUID(MAKISA), user_id=dono.id, platform="amazon", account_name="makisa"),
            StoreInfo(
                id=UUID(JLAS_ARQUIVADA),
                user_id=dono.id,
                platform="amazon",
                account_name="jlas",
                sales_team=101,
                archived_at=datetime(2026, 7, 7, tzinfo=UTC),
            ),
        ]
    )

    def _pessoa(nome: str, **campos) -> User:
        email = f"{nome}@davinci-test.com"
        return User(
            open_id=f"email:{email}",
            email=email,
            name=nome,
            role=campos.pop("role", UserRole.USER),
            status=UserStatus.ACTIVE,
            **campos,
        )

    # Sem `sales_teams` no construtor = SQL NULL (como o israel e o marrocos
    # estão na produção); `sales_teams=None` gravaria JSON null.
    db.add_all(
        [
            _pessoa(
                "londres",
                id=_usuario(mod, "londres"),
                sales_teams=[101, 102, 103],
                stock_tags=TAGS_LONDRES,
            ),
            _pessoa("israel", id=_usuario(mod, "israel"), stock_tags=TAGS_ISRAEL),
            _pessoa("marrocos", id=_usuario(mod, "marrocos"), stock_tags=TAGS_MARROCOS),
            _pessoa("azeroth", stock_tags=["ra"]),  # armazém: sem equipe, só a tag
            _pessoa("heisenberg", role=UserRole.ADMIN, sales_teams=[101]),
        ]
    )
    await db.commit()


async def _rodar(db, mod, passo: str) -> None:
    def _executar(conn) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, passo)()

    conn = await db.connection()
    await conn.run_sync(_executar)
    await db.commit()


async def _sql(db, consulta: str) -> dict:
    linhas = await db.execute(text(consulta.format(s=Base.metadata.schema)))
    return dict(linhas.all())


async def _lojas(db) -> dict[str, int | None]:
    return await _sql(db, 'SELECT id::text, sales_team FROM "{s}".store_info')


async def _versoes(db) -> dict[str, str]:
    """id → xmin: muda só quando a linha é gravada (o FOR UPDATE não muda)."""
    return await _sql(db, 'SELECT id::text, xmin::text FROM "{s}".store_info')


async def _pessoas(db) -> dict[str, tuple]:
    """nome → (sales_teams, tipo no banco — None = SQL NULL, stock_tags, papel)."""
    schema = Base.metadata.schema
    linhas = await db.execute(
        text(
            "SELECT name, sales_teams, jsonb_typeof(sales_teams), stock_tags, role::text"
            f' FROM "{schema}".users WHERE name IS NOT NULL'
        )
    )
    return {nome: tuple(resto) for nome, *resto in linhas.all()}


async def _estado(db) -> tuple:
    return await _lojas(db), await _pessoas(db)


async def test_0441_poe_lojas_e_usuarios_nas_equipes_e_o_downgrade_devolve(db, make_user, capsys):
    mod = _carregar()
    assert mod.revision == "0441_equipes_londres_israel_marrocos"
    assert mod.down_revision == "0440_atendimento_lojas_pessoa"
    # A lista: 79 ids únicos (77 da planilha + o nexus e o atv do ML); 36 mudam.
    assert len({id_ for id_, *_ in mod.LOJAS}) == len(mod.LOJAS) == 79
    assert Counter(depois for *_, depois in mod.LOJAS) == {101: 30, 102: 31, 103: 18}
    assert sum(1 for _, _, antes, depois in mod.LOJAS if antes != depois) == 36
    await _semear(db, mod, make_user)
    pessoas = await _pessoas(db)
    assert pessoas["israel"][:2] == pessoas["marrocos"][:2] == (None, None)  # SQL NULL
    lojas_30_10, pessoas_30_10 = await _estado(db)
    versoes = await _versoes(db)

    await _rodar(db, mod, "upgrade")
    lojas = await _lojas(db)
    for id_, conta, _antes, depois in mod.LOJAS:
        assert lojas[id_] == depois, conta
    assert lojas["ee2fedb3-d706-448d-a3d9-70a5651b74f7"] == 102  # o nexus do ML, arquivado
    assert lojas["5fd84133-5a17-4785-ba40-04e486b4638a"] == 102  # o atv do ML, arquivado
    assert (lojas[PRESENCIAL], lojas[MAKISA], lojas[JLAS_ARQUIVADA]) == (103, None, 101)
    # Gravadas: só as 36 que mudaram de equipe. As outras 43, que já estavam na
    # certa, só foram conferidas.
    depois_da_ida = await _versoes(db)
    gravadas = {id_ for id_ in versoes if versoes[id_] != depois_da_ida[id_]}
    mudaram = {id_ for id_, _, antes, depois in mod.LOJAS if antes != depois}
    assert gravadas == mudaram
    assert len(gravadas) == 36
    pessoas = await _pessoas(db)
    assert pessoas["londres"] == ([101], "array", TAGS_LONDRES, "user")
    assert pessoas["israel"] == ([102], "array", TAGS_ISRAEL, "user")
    assert pessoas["marrocos"] == ([103], "array", TAGS_MARROCOS, "user")
    assert pessoas["azeroth"] == (None, None, ["ra"], "user")  # armazém: nada muda
    assert pessoas["heisenberg"] == ([101], "array", None, "admin")  # admin: nada muda
    saida = capsys.readouterr().out
    assert "[0441] lojas: 36 mudaram de equipe, 43 já estavam na certa" in saida
    assert "[0441] londres: [101, 102, 103] → [101]" in saida
    assert "[0441] israel: sem equipe → [102]" in saida
    assert "@" not in saida  # nenhum e-mail na saída do deploy

    await _rodar(db, mod, "downgrade")
    assert await _estado(db) == (lojas_30_10, pessoas_30_10)
    pessoas = await _pessoas(db)
    assert pessoas["londres"] == ([101, 102, 103], "array", TAGS_LONDRES, "user")
    # Volta a SQL NULL (sem equipe = vê tudo), não a JSON null.
    assert pessoas["israel"] == (None, None, TAGS_ISRAEL, "user")
    assert pessoas["marrocos"] == (None, None, TAGS_MARROCOS, "user")
    # A volta não grava nas que a 0441 não mudou (já estavam certas em 30/10).
    depois_da_volta = await _versoes(db)
    for id_, conta, antes, depois in mod.LOJAS:
        if antes == depois:
            assert depois_da_volta[id_] == versoes[id_], conta
    saida = capsys.readouterr().out
    assert "[0441] volta: 36 loja(s) de volta à equipe de 30/10, 0 mexida(s) depois" in saida
    assert "[0441] volta: marrocos de volta a sem equipe" in saida
    assert "fica como está" not in saida


async def test_0441_aceita_quem_ja_esta_no_valor_novo(db, make_user, capsys):
    mod = _carregar()
    # O ml mega já foi mudado à mão para a Londres (era 102).
    await _semear(db, mod, make_user, equipes={"ml mega": 101})
    schema = Base.metadata.schema
    # A tela de Usuários grava JSON null ao esvaziar a lista: é "sem equipe".
    await db.execute(
        text(f"UPDATE \"{schema}\".users SET sales_teams = 'null'::jsonb WHERE name = 'israel'")
    )
    await db.execute(
        text(f"UPDATE \"{schema}\".users SET sales_teams = '[103]'::jsonb WHERE name = 'marrocos'")
    )
    await db.commit()
    ml_mega = _id_da_loja(mod, "ml mega")
    versoes = await _versoes(db)

    await _rodar(db, mod, "upgrade")
    lojas = await _lojas(db)
    for id_, conta, _antes, depois in mod.LOJAS:
        assert lojas[id_] == depois, conta
    # Já certa: só conferida, sem gravar.
    depois = await _versoes(db)
    assert depois[ml_mega] == versoes[ml_mega]
    pessoas = await _pessoas(db)
    assert [pessoas[n][0] for n in ("londres", "israel", "marrocos")] == [[101], [102], [103]]
    saida = capsys.readouterr().out
    assert "[0441] a loja ml mega já estava na 101 (mudada à mão)" in saida
    assert "[0441] lojas: 35 mudaram de equipe, 44 já estavam na certa" in saida
    assert "[0441] marrocos: já estava em [103]" in saida


@pytest.mark.parametrize(
    ("caso", "erro"),
    [
        ("loja_sumiu", "a loja temu eron .* não existe"),
        ("loja_em_outra_equipe", "a loja shopee fiore .* está na equipe 103"),
        ("usuario_virou_admin", "não é o marrocos"),
        ("usuario_com_outra_equipe", "o londres .* está com \\[102\\]"),
    ],
)
async def test_0441_para_inteira_e_nada_muda(db, make_user, caso, erro):
    mod = _carregar()
    # shopee fiore: era 102, vai para 101; uma terceira equipe é conflito.
    await _semear(
        db,
        mod,
        make_user,
        equipes={"shopee fiore": 103} if caso == "loja_em_outra_equipe" else None,
    )
    schema = Base.metadata.schema
    comando = {
        # a última da lista: as 78 de antes já teriam mudado
        "loja_sumiu": "DELETE FROM \"{s}\".store_info WHERE account_name = 'eron'"
        " AND platform = 'temu'",
        # os usuários vêm depois das lojas: tudo o que já mudou tem de voltar
        "usuario_virou_admin": "UPDATE \"{s}\".users SET role = 'admin' WHERE name = 'marrocos'",
        "usuario_com_outra_equipe": "UPDATE \"{s}\".users SET sales_teams = '[102]'::jsonb"
        " WHERE name = 'londres'",
    }.get(caso)
    if comando:
        await db.execute(text(comando.format(s=schema)))
        await db.commit()
    antes = await _estado(db)

    with pytest.raises(RuntimeError, match=erro):
        await _rodar(db, mod, "upgrade")
    await db.rollback()

    assert await _estado(db) == antes


async def test_0441_downgrade_so_desfaz_o_que_ninguem_mexeu_depois(db, make_user, capsys):
    mod = _carregar()
    await _semear(db, mod, make_user)
    lojas_30_10, _pessoas_30_10 = await _estado(db)
    await _rodar(db, mod, "upgrade")
    capsys.readouterr()

    # Depois da 0441, mexidas à mão na tela:
    schema = Base.metadata.schema
    ml_mega, vortan = _id_da_loja(mod, "ml mega"), _id_da_loja(mod, "shopee vortan")
    for comando in (
        # a 0441 mudou (102 → 101) e alguém passou para a Marrocos
        f"UPDATE \"{{s}}\".store_info SET sales_team = 103 WHERE id = '{ml_mega}'",
        # a 0441 NÃO mudou (101 em 30/10 e depois): a volta não "devolve" 101
        f"UPDATE \"{{s}}\".store_info SET sales_team = 102 WHERE id = '{vortan}'",
        # o israel ganhou mais uma equipe
        "UPDATE \"{s}\".users SET sales_teams = '[102, 103]'::jsonb WHERE name = 'israel'",
    ):
        await db.execute(text(comando.format(s=schema)))
    await db.commit()
    versoes = await _versoes(db)

    await _rodar(db, mod, "downgrade")
    lojas, pessoas = await _estado(db)
    # As mexidas depois ficam como estão — e nem são gravadas.
    assert lojas[ml_mega] == 103
    assert lojas[vortan] == 102
    depois = await _versoes(db)
    assert (depois[ml_mega], depois[vortan]) == (versoes[ml_mega], versoes[vortan])
    assert pessoas["israel"] == ([102, 103], "array", TAGS_ISRAEL, "user")
    # O resto volta ao retrato de 30/10.
    for id_, conta, *_ in mod.LOJAS:
        if id_ not in (ml_mega, vortan):
            assert lojas[id_] == lojas_30_10[id_], conta
    assert pessoas["londres"] == ([101, 102, 103], "array", TAGS_LONDRES, "user")
    assert pessoas["marrocos"] == (None, None, TAGS_MARROCOS, "user")
    saida = capsys.readouterr().out
    assert (
        "[0441] volta: a loja ml mega está na 103, não na 101 (mexida depois) — fica como está"
        in saida
    )
    assert "a loja shopee vortan" not in saida  # a 0441 não tinha mudado: nem conferida
    assert "[0441] volta: 35 loja(s) de volta à equipe de 30/10, 1 mexida(s) depois" in saida
    assert "[0441] volta: israel está com [102, 103], não [102] (mexido depois)" in saida
    assert "[0441] volta: londres de volta a [101, 102, 103]" in saida
    assert "@" not in saida


@pytest.mark.parametrize(
    ("caso", "comando", "erro"),
    [
        (
            "usuario_virou_admin",
            "UPDATE \"{s}\".users SET role = 'admin' WHERE name = 'marrocos'",
            "não é o marrocos",
        ),
        (
            "usuario_sumiu",
            "DELETE FROM \"{s}\".users WHERE name = 'israel'",
            "o usuário israel .* não existe",
        ),
        (
            "loja_sumiu",
            "DELETE FROM \"{s}\".store_info WHERE id = '5fd84133-5a17-4785-ba40-04e486b4638a'",
            "a loja mercadolivre atv .* não existe",
        ),
    ],
)
async def test_0441_downgrade_confere_a_identidade_e_para_inteiro(
    db, make_user, caso, comando, erro
):
    mod = _carregar()
    await _semear(db, mod, make_user)
    await _rodar(db, mod, "upgrade")
    await db.execute(text(comando.format(s=Base.metadata.schema)))
    await db.commit()
    antes = await _estado(db)

    with pytest.raises(RuntimeError, match=erro):
        await _rodar(db, mod, "downgrade")
    await db.rollback()

    assert await _estado(db) == antes, caso
