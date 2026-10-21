"""A 0424 (categoria da pasta criada, 21/10/2026): a semente, o CHECK, o estado
`informativo`, o chamado da Pós-venda na meta — e o downgrade.

Roda de verdade a 0386 (dele) + 0387 + 0389 + 0424 num schema descartável (o
conftest monta o de teste pelo model). O model de hoje = as quatro
(`test_mail_atendimento_migration.py` compara o catálogo inteiro); aqui o que
é só da 0424:
  • a semente é EXATAMENTE as decisões 5 e 9 do dono: só as pastas "vendas *"
    de marketplace viram `venda`, e só as pastas de site da Uranyx e da
    Charlots viram `site` ("Site (como hoje)") — as 25 pastas de produção,
    pelo que a regra de palavras já gravou, com as marcas do cadastro de
    produção (a `poofy` tem o nome "charlots" e o domínio charlots.com.br; não
    há marca `makisa`); tudo o mais fica NULL — nenhuma `chamado`, nenhuma
    `informativo`; a Entrada, os Enviados, a Lixeira e o Spam nunca;
  • o CHECK aceita só os quatro valores (os três tipos e `site`, "Site (como
    hoje)"); a meta aceita `informativo`;
  • as tabelas dos chamados NÃO mudam (a Pós-venda só acrescenta);
  • o downgrade: a linha informativa vira `ignorado`, a meta que só ficou
    guardada pela regra nova é apagada (a ponte de antes relê), as colunas saem.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Base
from tests.test_mail_atendimento_migration import REFERIDAS, _catalogo

_VERSOES = Path(__file__).resolve().parent.parent / "alembic" / "versions"
USER = "00000000-0000-0000-0000-000000000001"
CAIXA = "00000000-0000-0000-0000-0000000000aa"

# (nome, tipo_tuta, ler, finalidade, plataforma, finalidade_manual, plataforma_manual,
#  ignorar) — as 25 pastas de produção (21/10/2026, `scratchpad/emails/tela_pastas.out`),
# com o que a regra de palavras gravou nelas, e a categoria que a 0424 dá
# (os sites: "*uranyx sac" e "*poofy" — a Charlots — viram `site`; "*makisa" não).
PASTAS_DE_PRODUCAO = [
    ("vendas shopee", "0", "corpo", "vendas", "shopee", None, None, False, "venda"),
    ("vendas ml", "0", "corpo", "vendas", "ml", None, None, False, "venda"),
    ("vendas amazon", "0", "corpo", "vendas", "amazon", None, None, False, "venda"),
    ("vendas temu", "0", "corpo", "vendas", "temu", None, None, False, "venda"),
    ("vendas ali", "0", "corpo", "vendas", "aliexpress", None, None, False, "venda"),
    ("vendas magalu", "0", "corpo", "vendas", "magalu", None, None, False, "venda"),
    ("mensagens ml", "0", "corpo", "mensagens", "ml", None, None, False, None),
    ("mensagens amazon", "0", "corpo", "mensagens", "amazon", None, None, False, None),
    ("problema ml", "0", "corpo", "problema", "ml", None, None, False, None),
    ("problema shopee", "0", "corpo", "problema", "shopee", None, None, False, None),
    ("problema tiktok", "0", "corpo", "problema", "tiktok", None, None, False, None),
    ("*makisa", "0", "corpo", None, "site", None, None, False, None),
    ("*uranyx sac", "0", "corpo", None, "site", None, None, False, "site"),
    ("*poofy", "0", "corpo", None, "site", None, None, False, "site"),
    ("envio", "0", "so_contar", None, None, None, None, False, None),
    ("financeiro", "0", "so_contar", None, None, None, None, False, None),
    ("devoluçoes", "0", "so_contar", None, None, None, None, False, None),
    ("anuncio problema", "0", "so_contar", None, None, None, None, False, None),
    ("envio erro", "0", "so_contar", None, None, None, None, False, None),
    ("contabilidade", "0", "so_contar", None, None, None, None, False, None),
    ("INBOX", "1", "corpo", "entrada", None, None, None, False, None),
    ("INBOX goslin", "1", "corpo", "entrada", None, None, None, False, None),
    ("Sent", "2", "corpo", "enviados", None, None, None, False, None),
    ("Trash", "3", "so_contar", None, None, None, None, False, None),
    ("Spam", "5", "so_contar", None, None, None, None, False, None),
]
# Os casos de borda (não existem em produção): a escolha de PESSOA vale, a
# pasta ignorada e a "vendas" de site nunca são venda; o site é pela MARCA do
# cadastro (a Charlots Park pela palavra "charlots"; a 7buyers não; a pasta de
# site ignorada, a que só conta e a de plataforma escolhida à mão seguem a regra).
BORDAS = [
    ("vendas manual", "0", "corpo", None, None, "vendas", "shopee", False, "venda"),
    ("vendas ignorada", "0", "nao", "vendas", "ml", None, None, True, None),
    ("vendas site", "0", "corpo", "vendas", "site", None, None, False, None),
    ("vendas importada", "9", "corpo", "vendas", "ml", None, None, False, "venda"),
    ("*charlots atacado", "0", "corpo", None, "site", None, None, False, "site"),
    ("*7buyers", "0", "corpo", None, "site", None, None, False, None),
    ("*locagil sac", "0", "corpo", None, "site", None, None, False, None),
    ("*uranyx velha", "0", "nao", None, "site", None, None, True, None),
    ("*uranyx contada", "0", "so_contar", None, "site", None, None, False, None),
    ("uranyx à mão", "0", "corpo", None, None, None, "site", False, "site"),
    ("*uranyx entrada", "1", "corpo", "entrada", None, None, "site", False, None),
]
# O cadastro de marcas de produção (21/10/2026; slug, nome, domínio, domínio_br, ativa)
# + uma INATIVA com o slug `makisa` (e o domínio da Charlots): a ponte não a
# acha (só as ativas), então "*makisa" continua sem categoria.
MARCAS_DE_PRODUCAO = [
    ("12paxion", "12paxion", None, "12paxion.com.br", True),
    ("7buyers", "7buyers", "7buyers.com", "7buyers.com.br", True),
    (
        "charlots-park",
        "Charlots Park",
        "charlotspark.com",
        "charlotspark.com.br, charlots.com.br",
        True,
    ),
    ("locagil", "locagil", "locagil.com", "locagil.com.br", True),
    ("poofy", "charlots", None, "charlots.com.br", True),
    ("uranyx", "uranyx", "uranyx.com", "uranyx.com.br", True),
    ("makisa", "makisa antiga", None, "charlots.com.br", False),
]
DOS_CHAMADOS = ["chamados", "chamado_mensagem"]


def _carregar(nome: str):
    caminho = _VERSOES / nome
    spec = importlib.util.spec_from_file_location(f"migration_{caminho.stem}", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_uma_ponta_so_e_depois_dos_fatos():
    config = Config()
    config.set_main_option("script_location", str(_VERSOES.parent))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["0424_mail_pastas_categoria"]
    nossa = scripts.get_revision("0424_mail_pastas_categoria")
    assert nossa.down_revision == "0423_atendimento_fato_apple_kia"


def test_a_migration_nao_toca_nas_tabelas_dos_chamados():
    fonte = (_VERSOES / "0424_mail_pastas_categoria.py").read_text()
    codigo = fonte.split('"""', 2)[2]
    assert "chamado_mensagem" not in codigo
    assert '"chamados",' not in codigo.replace('"chamados",\n        ["chamado_id"]', "")


@pytest.mark.asyncio
async def test_semente_check_estado_e_downgrade(db: AsyncSession):
    rascunho = f"{Base.metadata.schema}_mig0424"
    dele = _carregar("0386_mail_central.py")
    camada = _carregar("0387_mail_atendimento.py")
    leitores = _carregar("0389_mail_leitores.py")
    nossa = _carregar("0424_mail_pastas_categoria.py")
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    for tabela in REFERIDAS:
        await db.execute(text(f'CREATE TABLE "{rascunho}".{tabela} (id uuid PRIMARY KEY)'))
    await db.execute(
        text(f'CREATE TABLE "{rascunho}".chamado_mensagem (id uuid PRIMARY KEY, texto text)')
    )
    await db.commit()

    def _rodar(conn, passos) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            for mod, passo in passos:
                getattr(mod, passo)()

    for mod in (dele, camada, leitores, nossa):
        mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, [(dele, "upgrade"), (camada, "upgrade"), (leitores, "upgrade")])
        await db.commit()
        await db.execute(text(f"INSERT INTO \"{rascunho}\".users (id) VALUES ('{USER}')"))
        # A tabela de marcas (o stub só tem o id) com o que a semente do site lê.
        await db.execute(
            text(
                f'ALTER TABLE "{rascunho}".marcas ADD COLUMN slug text, ADD COLUMN nome text,'
                " ADD COLUMN dominio text, ADD COLUMN dominio_br text, ADD COLUMN ativo boolean"
            )
        )
        for slug, nome, dominio, dominio_br, ativo in MARCAS_DE_PRODUCAO:
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".marcas (id, slug, nome, dominio, dominio_br, ativo)'
                    " VALUES (gen_random_uuid(), :s, :n, :d, :b, :a)"
                ),
                {"s": slug, "n": nome, "d": dominio, "b": dominio_br, "a": ativo},
            )
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".mail_mailboxes (id, owner_user_id, label, config_enc,'
                " agent_token_hash, send_enabled, agent_can_send, state) VALUES"
                f" ('{CAIXA}', '{USER}', 'x', '\\x00', repeat('0', 64), false, false, 'offline')"
            )
        )
        for n, linha in enumerate(PASTAS_DE_PRODUCAO + BORDAS):
            nome, tipo, ler, fin, plat, fin_m, plat_m, ignorar, _ = linha
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".mail_folders (id, mailbox_id, chave, nome,'
                    " tipo_tuta, ler, finalidade, plataforma, finalidade_manual,"
                    " plataforma_manual, ignorar, revisada) VALUES (gen_random_uuid(), :caixa,"
                    " :chave, :nome, :tipo, :ler, :fin, :plat, :fin_m, :plat_m, :ignorar,"
                    " false)"
                ),
                {
                    "caixa": CAIXA,
                    "chave": f"{n}:{nome}",
                    "nome": nome,
                    "tipo": tipo,
                    "ler": ler,
                    "fin": fin,
                    "plat": plat,
                    "fin_m": fin_m,
                    "plat_m": plat_m,
                    "ignorar": ignorar,
                },
            )
        await db.commit()
        dos_chamados_antes = await _catalogo(db, rascunho, DOS_CHAMADOS)
        conn = await db.connection()
        await conn.run_sync(_rodar, [(nossa, "upgrade")])
        await db.commit()

        linhas = (
            await db.execute(
                text(
                    "SELECT nome, categoria, categoria_por, categoria_em IS NOT NULL, revisada"
                    f' FROM "{rascunho}".mail_folders'
                )
            )
        ).all()
        por_nome = {r[0]: r[1:] for r in linhas}
        for linha in PASTAS_DE_PRODUCAO + BORDAS:
            nome, esperado = linha[0], linha[-1]
            categoria, por, em, revisada = por_nome[nome]
            assert categoria == esperado, nome
            assert por is None, nome  # foi a migration, nunca uma pessoa
            assert em is (esperado is not None) and revisada is (esperado is not None), nome
        # Decisão 5: só as 6 "vendas *" de produção; decisão 9: só os sites da
        # Uranyx e da Charlots; nada de chamado nem informativo.
        producao = {r[0]: r[-1] for r in PASTAS_DE_PRODUCAO}
        assert sorted(n for n, c in producao.items() if c == "venda") == sorted(
            [
                "vendas shopee",
                "vendas ml",
                "vendas amazon",
                "vendas temu",
                "vendas ali",
                "vendas magalu",
            ]
        )
        assert sorted(n for n, c in producao.items() if c == "site") == ["*poofy", "*uranyx sac"]
        assert {c for _, c, *_ in linhas} == {None, "venda", "site"}
        # O CHECK da categoria.
        with pytest.raises(Exception, match="ck_mail_folders_categoria"):
            await db.execute(text(f"UPDATE \"{rascunho}\".mail_folders SET categoria = 'outra'"))
        await db.rollback()
        for valor in ("informativo", "chamado", "venda", "site"):
            await db.execute(
                text(
                    f'UPDATE "{rascunho}".mail_folders SET categoria = :v'
                    " WHERE nome = 'mensagens ml'"
                ),
                {"v": valor},
            )
        await db.rollback()
        # As tabelas dos chamados saem da 0424 exatamente como entraram.
        assert await _catalogo(db, rascunho, DOS_CHAMADOS) == dos_chamados_antes

        # A meta aceita `informativo` (e o chamado da Pós-venda).
        msgs = []
        for n in range(4):
            mid = f"00000000-0000-0000-0000-00000000010{n}"
            msgs.append(mid)
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".mail_messages (id, mailbox_id, source_id,'
                    " direction, received_at, content_enc, attachment_count) VALUES"
                    f" ('{mid}', '{CAIXA}', 's{n}', 'inbound', now(), '\\x00', 0)"
                )
            )
        await db.execute(text(f"INSERT INTO \"{rascunho}\".chamados (id) VALUES ('{USER}')"))
        for mid, estado, motivo, chamado in (
            (msgs[0], "informativo", None, None),
            (msgs[1], "ignorado", "pasta_sem_categoria", None),
            (msgs[2], "ignorado", "chamado_sem_chamado", None),
            (msgs[3], "gravado", None, USER),
        ):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".mail_message_meta (message_id, mailbox_id,'
                    " estado, motivo, chamado_id) VALUES (:m, :c, :e, :mo, :ch)"
                ),
                {"m": mid, "c": CAIXA, "e": estado, "mo": motivo, "ch": chamado},
            )
        await db.commit()
        with pytest.raises(Exception, match="ck_mail_message_meta_estado"):
            await db.execute(text(f"UPDATE \"{rascunho}\".mail_message_meta SET estado = 'outro'"))
        await db.rollback()

        # O downgrade: a linha informativa vira ignorado; as guardadas pela regra
        # nova somem (a ponte de antes relê); o ligado ao chamado fica (sem o id).
        conn = await db.connection()
        await conn.run_sync(_rodar, [(nossa, "downgrade")])
        await db.commit()
        metas = dict(
            (
                await db.execute(
                    text(
                        "SELECT message_id::text, estado || '/' || coalesce(motivo, '')"
                        f' FROM "{rascunho}".mail_message_meta'
                    )
                )
            ).all()
        )
        assert metas == {msgs[0]: "ignorado/pasta_informativa", msgs[3]: "gravado/"}
        colunas = {
            r[0]
            for r in (
                await db.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_schema = :s AND table_name IN"
                        " ('mail_folders', 'mail_message_meta')"
                    ),
                    {"s": rascunho},
                )
            ).all()
        }
        assert not {"categoria", "categoria_por", "categoria_em", "chamado_id"} & colunas
        with pytest.raises(Exception, match="ck_mail_message_meta_estado"):
            await db.execute(
                text(f"UPDATE \"{rascunho}\".mail_message_meta SET estado = 'informativo'")
            )
        await db.rollback()
        assert await _catalogo(db, rascunho, DOS_CHAMADOS) == dos_chamados_antes
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()


def test_site_como_hoje_pela_marca_do_cadastro():
    """Decisão 9: a marca é a da Uranyx ou a da Charlots pelo slug, pelo nome ou
    pelo domínio (a `poofy` de produção: nome "charlots", charlots.com.br); a
    semente das palavras (tabela de regras vazia) e as palavras do nome são as
    do código de hoje (`regras`)."""
    from app.services.mail_atendimento import regras

    nossa = _carregar("0424_mail_pastas_categoria.py")
    sim = (
        ("uranyx", "uranyx", nossa._dominios("uranyx.com.br", "uranyx.com")),
        ("poofy", "charlots", nossa._dominios("charlots.com.br", None)),
        (
            "charlots-park",
            "Charlots Park",
            nossa._dominios("charlotspark.com.br, charlots.com.br", "charlotspark.com"),
        ),
    )
    nao = (
        ("7buyers", "7buyers", nossa._dominios("7buyers.com.br", "7buyers.com")),
        ("locagil", "locagil", nossa._dominios("locagil.com.br", "https://www.locagil.com/")),
        ("12paxion", "12paxion", nossa._dominios("12paxion.com.br", None)),
    )
    for slug, nome, dominios in sim:
        assert nossa._e_site_como_hoje(slug, nome, dominios), slug
    for slug, nome, dominios in nao:
        assert not nossa._e_site_como_hoje(slug, nome, dominios), slug
    assert nossa._dominios("https://www.locagil.com/", None) == ["locagil.com"]
    assert dict(nossa.MARCAS_DA_SEMENTE) == regras.regras_padrao().marca
    for nome in ("*Uranyx SAC", "*charlots atacado", "uranyx à mão", "*poofy"):
        assert nossa._palavras(nome) == regras.palavras(nome), nome
