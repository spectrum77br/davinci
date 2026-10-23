"""O esquema do AGENTE de atendimento (20/10/2026, migration 0420).

O que se confere aqui:
  • a 0420 cria EXATAMENTE o que o model declara (as quatro tabelas novas e
    as duas colunas do canal), num schema descartável, e o downgrade desfaz;
  • a semente dos fatos: os aprovados nascem ativos (com quem aprovou), os
    candidatos não, o que depende de loja/produto só nasce se eles existem,
    rodar de novo não duplica e o `tsv` é o mesmo que a aplicação calcula;
  • os models gravam e leem, o `tsv` é preenchido pelo ORM e acha a pergunta
    sem acento e sem caixa, e os CHECKs dos fatos seguram o que não pode;
  • a busca por texto (`busca_texto`): o mesmo jeito de escrever dos dois lados;
  • quando o agente roda num canal (`agente_config`): a matriz inteira;
  • a correção do "entregue" (`contexto.localizacao_diz_entregue`);
  • as constantes do agente, a configuração e o texto de `cobertura_regiao`.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import SecretStr
from sqlalchemy import Text, cast, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import AtendimentoCanal, AtendimentoConversa, AtendimentoMensagem, Base, Logistica
from app.models.atendimento import (
    AtendimentoAgenteReplay,
    AtendimentoFato,
    AtendimentoPar,
    AtendimentoSiteProduto,
)
from app.services.atendimento import agente_config, busca_texto, constantes, contexto
from app.services.atendimento.busca_texto import consulta_de, texto_de_busca, tsv_de

_API = Path(__file__).resolve().parent.parent
_MIGRATION = _API / "alembic" / "versions" / "0420_atendimento_agente.py"
NOVAS = (
    "atendimento_agente_replay",
    "atendimento_fatos",
    "atendimento_pares",
    "atendimento_site_produtos",
)
WATCH_LOJA = "2ac2633b-c698-4718-99cb-6e5443a80689"
PULSEIRA = "df0823b8-0674-4f50-896c-179364acb823"


def _carregar_migration():
    spec = importlib.util.spec_from_file_location("migration_0420_agente", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
async def _limpa_tabelas_do_agente(db: AsyncSession):
    # O conftest não limpa as tabelas novas sem FK para o que ele limpa.
    async def limpar():
        for modelo in (AtendimentoFato, AtendimentoSiteProduto, AtendimentoAgenteReplay):
            await db.execute(delete(modelo))
        await db.commit()

    await limpar()
    yield
    await limpar()


# ─────────────── migration ───────────────


async def _catalogo(db: AsyncSession, schema: str) -> dict[str, list]:
    """Colunas, constraints e índices das tabelas novas + as colunas novas do canal."""

    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "") if s else s

    colunas = (
        await db.execute(
            text(
                """
                SELECT table_name, column_name, udt_name, character_maximum_length,
                       is_nullable, column_default
                  FROM information_schema.columns
                 WHERE table_schema = :schema
                   AND (table_name = ANY(:novas)
                        OR (table_name = 'atendimento_canais'
                            AND column_name IN ('agente', 'auto_orientacao', 'auto_agente')))
                 ORDER BY table_name, column_name
                """
            ),
            {"schema": schema, "novas": list(NOVAS)},
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                """
                SELECT cl.relname, co.conname, co.contype::text, pg_get_constraintdef(co.oid)
                  FROM pg_constraint co
                  JOIN pg_class cl ON cl.oid = co.conrelid
                  JOIN pg_namespace n ON n.oid = cl.relnamespace
                 WHERE n.nspname = :schema AND cl.relname = ANY(:novas)
                 ORDER BY cl.relname, co.conname
                """
            ),
            {"schema": schema, "novas": list(NOVAS)},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                """
                SELECT tablename, indexname, indexdef
                  FROM pg_indexes
                 WHERE schemaname = :schema AND tablename = ANY(:novas)
                 ORDER BY tablename, indexname
                """
            ),
            {"schema": schema, "novas": list(NOVAS)},
        )
    ).all()
    return {
        "tabelas": sorted({r[0] for r in colunas}),
        "colunas": [tuple(r[:5]) + (limpo(r[5]),) for r in colunas],
        "constraints": [(r[0], r[1], r[2], limpo(r[3])) for r in constraints],
        "indices": [(r[0], r[1], limpo(r[2])) for r in indices],
    }


def _rodar(conn, passo: str, mod) -> None:
    ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
    with Operations.context(ctx):
        getattr(mod, passo)()


def test_migration_0420_e_a_unica_ponta():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    mod = _carregar_migration()
    assert mod.revision == "0420_atendimento_agente"
    assert mod.down_revision == "0419_londres_logistica_editar"
    cfg = Config(str(_API / "alembic.ini"))
    cfg.set_main_option("script_location", str(_API / "alembic"))
    scripts = ScriptDirectory.from_config(cfg)
    # Uma ponta só (não necessariamente a 0420: o main vai ganhar outras), e a
    # 0420 está na árvore, em cima da 0419.
    assert len(scripts.get_heads()) == 1, scripts.get_heads()
    assert scripts.get_revision("0420_atendimento_agente").down_revision == (
        "0419_londres_logistica_editar"
    )


@pytest.mark.asyncio
async def test_migration_bate_com_o_model_semeia_e_o_downgrade_desfaz(db: AsyncSession):
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0420"
    mod = _carregar_migration()
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que as FKs da 0420 referenciam (o canal já existe: ganha duas colunas).
    for tabela in (
        "integrations",
        "products",
        "atendimento_canais",
        "atendimento_conversas",
        "atendimento_mensagens",
    ):
        await db.execute(text(f'CREATE TABLE "{rascunho}".{tabela} (id uuid PRIMARY KEY)'))
    # A loja do Apple Watch e UM dos cinco produtos da pulseira existem; os
    # outros quatro não (o fato deles não nasce).
    await db.execute(
        text(f'INSERT INTO "{rascunho}".integrations (id) VALUES (CAST(:i AS uuid))'),
        {"i": WATCH_LOJA},
    )
    await db.execute(
        text(f'INSERT INTO "{rascunho}".products (id) VALUES (CAST(:p AS uuid))'),
        {"p": PULSEIRA},
    )
    await db.execute(
        text(f'INSERT INTO "{rascunho}".atendimento_canais (id) VALUES (gen_random_uuid())')
    )
    await db.commit()
    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade", mod)
        await db.commit()

        da_migration = await _catalogo(db, rascunho)
        do_model = await _catalogo(db, schema_model)
        assert da_migration["tabelas"] == sorted([*NOVAS, "atendimento_canais"])
        # Comparação que não compara nada passaria calada: o catálogo tem corpo.
        assert len(da_migration["colunas"]) > 60
        assert da_migration["colunas"] == do_model["colunas"]
        assert da_migration["constraints"] == do_model["constraints"]
        assert da_migration["indices"] == do_model["indices"]
        nomes = {c[1] for c in da_migration["constraints"]}
        assert {
            "pk_atendimento_fatos",
            "uq_atendimento_fatos_chave",
            "ck_atendimento_fatos_escopo",
            "ck_atendimento_fatos_fonte",
            "ck_atendimento_fatos_escopo_preenchido",
            "ck_atendimento_fatos_ativo_aprovado",
            "fk_atendimento_fatos_integration_id_integrations",
            "fk_atendimento_fatos_product_id_products",
            "uq_atendimento_pares_conversa_id_resposta_msg_id",
            "fk_atendimento_pares_conversa_id_atendimento_conversas",
            "fk_atendimento_pares_resposta_msg_id_atendimento_mensagens",
            "fk_atendimento_pares_canal_id_atendimento_canais",
            "uq_atendimento_agente_replay_dia_gatilho_id",
            "fk_atendimento_agente_replay_conversa_id_atendimento_conversas",
            "uq_atendimento_site_produtos_marca_slug",
        } <= nomes
        restricoes = {c[1]: c[3] for c in da_migration["constraints"]}
        assert (
            "ON DELETE CASCADE"
            in restricoes["fk_atendimento_pares_conversa_id_atendimento_conversas"]
        )
        assert (
            "ON DELETE SET NULL" in restricoes["fk_atendimento_pares_canal_id_atendimento_canais"]
        )
        defs = {nome: d for _, nome, d in da_migration["indices"]}
        assert "USING gin (tsv)" in defs["ix_atendimento_fatos_tsv"]
        assert "USING gin (tsv)" in defs["ix_atendimento_pares_tsv"]
        assert (
            "(plataforma, integration_id, item_id)"
            in defs["ix_atendimento_pares_plataforma_integration_id_item_id"]
        )
        cols = {(c[0], c[1]): c for c in da_migration["colunas"]}
        # O canal que já existia nasce SEM agente e sem orientação no automático.
        assert cols[("atendimento_canais", "agente")][4:] == ("NO", "false")
        assert cols[("atendimento_canais", "auto_orientacao")][4:] == ("NO", "'[]'::jsonb")
        # E sem assunto em que o agente sai sozinho (decisão 8, revisão de 20/10/2026).
        assert cols[("atendimento_canais", "auto_agente")][4:] == ("NO", "'[]'::jsonb")
        assert tuple(
            (
                await db.execute(
                    text(
                        f"SELECT agente, auto_orientacao, auto_agente "
                        f'FROM "{rascunho}".atendimento_canais'
                    )
                )
            ).one()
        ) == (False, [], [])
        assert cols[("atendimento_fatos", "ativo")][4:] == ("NO", "false")
        assert cols[("atendimento_fatos", "tsv")][2:5] == ("tsvector", None, "NO")

        # A semente.
        sql_semente = (
            "SELECT chave, escopo, marca, assunto, fonte, ativo, aprovado_por, "
            "aprovado_em IS NOT NULL, texto, tsv::text, plataforma, item_id, "
            f'integration_id::text, product_id::text FROM "{rascunho}".atendimento_fatos'
        )
        semente = {r[0]: r for r in (await db.execute(text(sql_semente))).all()}
        ativos = {c for c, r in semente.items() if r[5]}
        candidatos = {c for c, r in semente.items() if not r[5]}
        assert ativos == {
            "celular_desbloqueado",
            "celular_ram_virtual",
            "celular_wifi_5ghz",
            "uranyx_s5_nfc",
            "mala_codigo_m",
            "airfryer_nao_bivolt",
            "origem_envio_shopee_22299473628",
            f"pulseira_padrao:{PULSEIRA}",
        }
        assert candidatos == {
            "capa_outro_modelo",
            "nf_vai_junto",
            "pecas_avulsas",
            "atualizacao_sistema",
            "onde_vai_o_chip",
            "remetente_etiqueta",
            "recusa_desconto",
            "recusa_brinde_amostra",
            "recusa_troca_pagando_diferenca",
            # O prazo de garantia do site é texto de política (decisão 4):
            # candidato até o dono aprovar (revisão de 20/10/2026).
            "charlots_garantia_90_dias",
        }
        # Ativo sempre com quem aprovou e quando; candidato sem nenhum dos dois.
        for chave in ativos:
            assert semente[chave][6] and semente[chave][7], chave
        for chave in candidatos:
            assert semente[chave][6] is None and semente[chave][7] is False, chave
        assert semente["celular_desbloqueado"][6] == "regra aprovada no código"
        charlots = semente["charlots_garantia_90_dias"]
        assert charlots[1:6] == ("marca", "charlots", "garantia", "site", False)
        assert charlots[6] is None
        assert "90 dias contra defeitos de fabricação" in charlots[8]
        watch = semente["origem_envio_shopee_22299473628"]
        assert (watch[1], watch[3], watch[10], watch[11], watch[12]) == (
            "anuncio",
            None,
            "shopee",
            "22299473628",
            WATCH_LOJA,
        )
        assert semente[f"pulseira_padrao:{PULSEIRA}"][13] == PULSEIRA
        assert {semente[c][4] for c in candidatos if c.startswith("recusa_")} == {"recusa_padrao"}
        # Texto para o cliente: sem R$ e sem prazo em número (fora os 90 dias,
        # que vêm do site da Charlots).
        for chave, linha in semente.items():
            assert "R$" not in linha[8], chave
            if chave != "charlots_garantia_90_dias":
                assert not re.search(r"\d+\s*(?:dias?|horas?|meses|mês)\b", linha[8]), chave
        # O `tsv` da semente é o mesmo que a aplicação calcula para o texto.
        for chave, linha in semente.items():
            esperado = await db.scalar(select(cast(tsv_de(linha[8]), Text)))
            assert linha[9] == esperado, chave

        # Idempotente: semear de novo não duplica nem muda.
        conn = await db.connection()
        await conn.run_sync(_rodar, "_semear", mod)
        await db.commit()
        assert (
            await db.scalar(text(f'SELECT count(*) FROM "{rascunho}".atendimento_fatos'))
        ) == len(semente)

        # O CHECK do banco: fato ativo sem quem aprovou não entra.
        with pytest.raises(IntegrityError, match="ck_atendimento_fatos_ativo_aprovado"):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".atendimento_fatos (texto, fonte, ativo, tsv) '
                    "VALUES ('x', 'manual', true, ''::tsvector)"
                )
            )
        await db.rollback()

        # O downgrade tira as quatro tabelas e as duas colunas do canal.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", mod)
        await db.commit()
        depois = await _catalogo(db, rascunho)
        assert depois["tabelas"] == []
        sobra = (
            (
                await db.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = 'atendimento_canais'"
                    ),
                    {"s": rascunho},
                )
            )
            .scalars()
            .all()
        )
        assert sobra == ["id"]
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()


# ─────────────── models ───────────────


async def _fato(db: AsyncSession, **campos) -> AtendimentoFato:
    padrao = {"texto": "A air fryer Uranyx não é bivolt.", "fonte": "manual"}
    fato = AtendimentoFato(**{**padrao, **campos})
    db.add(fato)
    await db.commit()
    return fato


async def _acha(db: AsyncSession, modelo, pergunta: str, **kw) -> list:
    consulta = consulta_de(pergunta, **kw)
    return (
        (await db.execute(select(modelo.id).where(modelo.tsv.op("@@")(consulta)))).scalars().all()
    )


async def test_fato_grava_com_tsv_do_orm_e_acha_sem_acento(db: AsyncSession):
    fato = await _fato(db, escopo="marca", marca="uranyx", assunto="espec_eletro")
    assert isinstance(fato.id, int)
    linha = (
        await db.execute(
            select(
                AtendimentoFato.escopo,
                AtendimentoFato.ativo,
                AtendimentoFato.aprovado_por,
                cast(AtendimentoFato.tsv, Text),
            ).where(AtendimentoFato.id == fato.id)
        )
    ).one()
    # Nasce candidato; o `tsv` foi calculado pelo ORM (o texto sem acento).
    assert linha[:3] == ("marca", False, None)
    assert "'nao'" in linha[3] and "'bivolt'" in linha[3]
    assert await _acha(db, AtendimentoFato, "Ela é BIVOLT?") == [fato.id]
    assert await _acha(db, AtendimentoFato, "airfryer nao e bivolt") == [fato.id]
    assert await _acha(db, AtendimentoFato, "geladeira") == []


async def test_fato_recalcula_o_tsv_quando_o_texto_muda(db: AsyncSession):
    fato = await _fato(db)
    fato.texto = "Capinha e película de outro modelo não servem."
    await db.commit()
    assert await _acha(db, AtendimentoFato, "capinha") == [fato.id]
    assert await _acha(db, AtendimentoFato, "bivolt") == []
    # Mudar outra coluna não mexe no `tsv`.
    fato.ativo, fato.aprovado_por = True, "Eduardo"
    fato.aprovado_em = datetime.now(UTC)
    await db.commit()
    assert await _acha(db, AtendimentoFato, "película") == [fato.id]


@pytest.mark.parametrize(
    ("campos", "trava"),
    [
        ({"ativo": True}, "ck_atendimento_fatos_ativo_aprovado"),
        ({"escopo": "loja"}, "ck_atendimento_fatos_escopo_preenchido"),
        ({"escopo": "anuncio", "item_id": "123"}, "ck_atendimento_fatos_escopo_preenchido"),
        ({"escopo": "marca"}, "ck_atendimento_fatos_escopo_preenchido"),
        ({"escopo": "sku"}, "ck_atendimento_fatos_escopo_preenchido"),
        ({"escopo": "produto"}, "ck_atendimento_fatos_escopo_preenchido"),
        ({"escopo": "plataforma"}, "ck_atendimento_fatos_escopo_preenchido"),
        ({"escopo": "cidade"}, "ck_atendimento_fatos_escopo"),
        ({"fonte": "chute"}, "ck_atendimento_fatos_fonte"),
    ],
)
async def test_fato_os_checks_seguram(db: AsyncSession, campos, trava):
    with pytest.raises(IntegrityError, match=trava):
        await _fato(db, **campos)
    await db.rollback()


async def test_fato_chave_e_unica(db: AsyncSession):
    await _fato(db, chave="x1")
    with pytest.raises(IntegrityError, match="uq_atendimento_fatos_chave"):
        await _fato(db, chave="x1")
    await db.rollback()
    # Sem chave (o fato da tela), quantos quiser.
    await _fato(db)
    await _fato(db)


async def _conversa_com_resposta(db: AsyncSession):
    canal = AtendimentoCanal(robo_perfil_id=f"p{uuid4().hex[:8]}", plataforma="temu", canal="chat")
    db.add(canal)
    await db.flush()
    conversa = AtendimentoConversa(
        canal_id=canal.id, plataforma="temu", canal="chat", externo_id=uuid4().hex
    )
    db.add(conversa)
    await db.flush()
    resposta = AtendimentoMensagem(
        conversa_id=conversa.id, autor="loja", origem="externo", texto="Oi"
    )
    db.add(resposta)
    await db.commit()
    return canal, conversa, resposta


async def test_canal_nasce_sem_agente(db: AsyncSession):
    canal, _, _ = await _conversa_com_resposta(db)
    await db.refresh(canal)
    assert canal.agente is False
    assert canal.auto_orientacao == [] and canal.auto_agente == []
    canal.agente, canal.auto_orientacao, canal.auto_agente = True, ["cancelamento"], ["rastreio"]
    await db.commit()
    await db.refresh(canal)
    assert (canal.agente, canal.auto_orientacao, canal.auto_agente) == (
        True,
        ["cancelamento"],
        ["rastreio"],
    )


async def test_par_pelo_orm_e_pelo_core_e_cascata(db: AsyncSession):
    canal, conversa, resposta = await _conversa_com_resposta(db)
    # Os ids antes: o rollback do meio expira os objetos da sessão.
    conversa_id, resposta_id, canal_id = conversa.id, resposta.id, canal.id
    agora = datetime.now(UTC)
    par = AtendimentoPar(
        conversa_id=conversa_id,
        resposta_msg_id=resposta_id,
        plataforma="temu",
        canal_id=canal_id,
        item_id="999",
        pergunta_gen="Vocês enviam para o Piauí?",
        resposta_gen="Conforme o anúncio, a disponibilidade aparece ao informar o CEP.",
        fonte=constantes.PAR_FONTE_EXTERNO,
        respondido_em=agora,
    )
    db.add(par)
    await db.commit()
    await db.refresh(par)
    assert (par.peso, par.autor_admin) == (1.0, False)
    # A pergunta pesa A, a resposta B.
    tsv = await db.scalar(select(cast(AtendimentoPar.tsv, Text)).where(AtendimentoPar.id == par.id))
    assert re.search(r":\d+A", tsv) and re.search(r":\d+B", tsv)
    assert await _acha(db, AtendimentoPar, "envia pro PIAUÍ?") == [par.id]

    # A mesma resposta de novo: o UNIQUE segura.
    db.add(
        AtendimentoPar(
            conversa_id=conversa_id,
            resposta_msg_id=resposta_id,
            plataforma="temu",
            pergunta_gen="x",
            resposta_gen="y",
            fonte="externo",
            respondido_em=agora,
        )
    )
    with pytest.raises(IntegrityError, match="uq_atendimento_pares_conversa_id_resposta_msg_id"):
        await db.commit()
    await db.rollback()

    # Pelo Core (o upsert da indexação) o `tsv` vem de `tsv_de`; sem ele, NOT NULL.
    outra = AtendimentoMensagem(conversa_id=conversa_id, autor="loja", origem="externo")
    db.add(outra)
    await db.commit()
    from sqlalchemy.dialects.postgresql import insert

    valores = {
        "conversa_id": conversa_id,
        "resposta_msg_id": outra.id,
        "plataforma": "temu",
        "pergunta_gen": "Tem na cor rosa?",
        "resposta_gen": "As cores disponíveis aparecem no anúncio.",
        "fonte": "davinci_humano",
        "respondido_em": agora,
    }
    with pytest.raises(IntegrityError, match="tsv"):
        await db.execute(insert(AtendimentoPar).values(**valores))
    await db.rollback()
    await db.execute(
        insert(AtendimentoPar).values(
            **valores, tsv=tsv_de(valores["pergunta_gen"], valores["resposta_gen"])
        )
    )
    await db.commit()
    assert len(await _acha(db, AtendimentoPar, "cor rosa")) == 1

    # Apagar a conversa leva os pares.
    await db.execute(delete(AtendimentoConversa).where(AtendimentoConversa.id == conversa_id))
    await db.commit()
    assert await db.scalar(select(AtendimentoPar.id)) is None


async def test_replay_e_site_produto(db: AsyncSession):
    _, conversa, resposta = await _conversa_com_resposta(db)
    replay = AtendimentoAgenteReplay(
        dia=date(2026, 10, 19),
        conversa_id=conversa.id,
        gatilho_id=resposta.id,
        gatilho_em=datetime(2026, 10, 19, 15, tzinfo=UTC),
        plataforma="shopee",
        proposta={"acao": "responder", "texto": "Olá! ..."},
        modelo="claude-haiku-5-5",
        effort="medium",
        parada="end_turn",
        custo_usd=Decimal("0.0026"),
    )
    db.add(replay)
    await db.commit()
    await db.refresh(replay)
    assert replay.criado_em is not None
    assert (replay.decisao, replay.rastro, replay.uso) == ({}, {}, {})
    db.add(
        AtendimentoAgenteReplay(
            dia=date(2026, 10, 19),
            conversa_id=conversa.id,
            gatilho_id=resposta.id,
            gatilho_em=datetime(2026, 10, 19, 15, tzinfo=UTC),
            plataforma="shopee",
        )
    )
    with pytest.raises(IntegrityError, match="uq_atendimento_agente_replay_dia_gatilho_id"):
        await db.commit()
    await db.rollback()

    pagina = AtendimentoSiteProduto(
        marca="uranyx",
        slug="f110-pro-5g",
        url="https://uranyx.com.br/produto/f110-pro-5g",
        titulo="F110 Pro 5G",
        modelo_chave="f110 pro 5g",
        specs=[{"rotulo": "Memória RAM", "valor": "20 GB (8 + até 12 virtual)"}],
        lido_em=datetime.now(UTC),
        hash="a" * 64,
    )
    db.add(pagina)
    await db.commit()
    lida = await db.scalar(
        select(AtendimentoSiteProduto).where(AtendimentoSiteProduto.slug == "f110-pro-5g")
    )
    assert lida.specs[0]["rotulo"] == "Memória RAM"
    db.add(
        AtendimentoSiteProduto(
            marca="uranyx",
            slug="f110-pro-5g",
            url="u",
            titulo="t",
            lido_em=datetime.now(UTC),
            hash="b",
        )
    )
    with pytest.raises(IntegrityError, match="uq_atendimento_site_produtos_marca_slug"):
        await db.commit()
    await db.rollback()


# ─────────────── busca por texto ───────────────


def test_texto_de_busca_tira_acento_e_caixa():
    assert texto_de_busca("  Não é  BIVOLT?\n") == "nao e bivolt?"
    assert texto_de_busca("Película, PIAUÍ, coração") == "pelicula, piaui, coracao"
    assert texto_de_busca(None) == ""


def test_palavras_de_busca_sem_operador_sem_repeticao_e_com_teto():
    assert busca_texto.palavras_de_busca("a & b | !c (d) 'x' <-> é é") == [
        "a",
        "b",
        "c",
        "d",
        "x",
        "e",
    ]
    muitas = " ".join(f"p{i}" for i in range(100))
    assert len(busca_texto.palavras_de_busca(muitas)) == busca_texto.MAX_PALAVRAS_CONSULTA
    assert consulta_de("") is None
    assert consulta_de("?!...") is None
    assert consulta_de("   ", todas=True) is None


async def _bate(db: AsyncSession, texto: str, pergunta: str, **kw) -> bool:
    return bool(await db.scalar(select(tsv_de(texto).op("@@")(consulta_de(pergunta, **kw)))))


async def test_consulta_qualquer_palavra_e_todas(db: AsyncSession):
    texto = "A air fryer Uranyx não é bivolt."
    assert await _bate(db, texto, "air fryer geladeira")
    assert not await _bate(db, texto, "air fryer geladeira", todas=True)
    assert await _bate(db, texto, "AIR fryer BIVOLT", todas=True)
    # Operadores do `to_tsquery` na pergunta não quebram a consulta.
    assert await _bate(db, texto, "bivolt & ) ( | ! :* <-> 'x")
    # Só palavras vazias do português: não acha nada, mas não dá erro.
    assert not await _bate(db, texto, "de o que")


async def test_tsv_de_pesos_e_partes(db: AsyncSession):
    duas = await db.scalar(select(cast(tsv_de("geladeira", "fogão"), Text)))
    assert re.search(r":1A\b", duas) and re.search(r":2B\b", duas)
    cinco = await db.scalar(select(cast(tsv_de("a1", "b2", "c3", "d4", "e5"), Text)))
    # As excedentes vão juntas na última parte (peso D — o padrão do Postgres,
    # que o texto do tsvector não escreve).
    assert "'a1':1A" in cinco and "'b2':2B" in cinco and "'c3':3C" in cinco
    assert "'d4':4" in cinco and "'e5':5" in cinco
    assert not re.search(r":[45][A-C]", cinco)
    vazio = await db.scalar(select(cast(tsv_de(None), Text)))
    assert vazio == ""


# ─────────────── quando o agente roda ───────────────


def _settings(**kw):
    base = {
        "atendimento_agente_ativo": True,
        "atendimento_agente_api_key": "",
        "atendimento_llm_api_key": "",
        "atendimento_agente_replay_plataformas": "shopee",
    }
    return SimpleNamespace(**{**base, **kw})


@pytest.mark.parametrize(
    ("agente", "llm", "esperado"),
    [
        ("sk-ant-agente", "", "sk-ant-agente"),
        ("sk-ant-agente", "sk-ant-llm", "sk-ant-agente"),
        ("", "sk-ant-llm", "sk-ant-llm"),
        # A do agente não é Claude: cai na do LLM se ELA for Claude.
        ("gsk_groq", "sk-ant-llm", "sk-ant-llm"),
        # Nunca a do Groq.
        ("", "gsk_groq", None),
        ("gsk_groq", "gsk_groq", None),
        ("", "", None),
        ("  sk-ant-espaco  ", "", "sk-ant-espaco"),
        ("SK-ANT-maiuscula", "", None),
    ],
)
def test_chave_do_agente(agente, llm, esperado):
    s = _settings(atendimento_agente_api_key=agente, atendimento_llm_api_key=llm)
    assert agente_config.chave_do_agente(s) == esperado


def test_chave_do_agente_aceita_secretstr():
    s = _settings(atendimento_llm_api_key=SecretStr("sk-ant-segredo"))
    assert agente_config.chave_do_agente(s) == "sk-ant-segredo"


@pytest.mark.parametrize("ativo", [True, False])
@pytest.mark.parametrize("com_chave", [True, False])
@pytest.mark.parametrize("no_canal", [True, False, None])
def test_agente_ligado_matriz(ativo, com_chave, no_canal):
    s = _settings(
        atendimento_agente_ativo=ativo,
        atendimento_agente_api_key="sk-ant-x" if com_chave else "",
    )
    canal = None if no_canal is None else SimpleNamespace(agente=no_canal)
    assert agente_config.agente_ligado(s, canal) is (ativo and com_chave and no_canal is True)


def test_agente_ligado_com_dicionario_e_valor_torto():
    s = _settings(atendimento_llm_api_key="sk-ant-x")
    assert agente_config.agente_ligado(s, {"agente": True}) is True
    assert agente_config.agente_ligado(s, {}) is False
    # Só o booleano verdadeiro liga (texto ou número da tela não).
    assert agente_config.agente_ligado(s, SimpleNamespace(agente="true")) is False
    assert agente_config.agente_ligado(s, SimpleNamespace(agente=1)) is False


def test_plataformas_do_replay():
    assert agente_config.plataformas_do_replay(
        _settings(atendimento_agente_replay_plataformas=" Shopee, tiktok,, ")
    ) == frozenset({"shopee", "tiktok"})
    assert (
        agente_config.plataformas_do_replay(_settings(atendimento_agente_replay_plataformas=""))
        == frozenset()
    )


def test_configuracao_padrao_do_agente():
    # O padrão do código (não o .env de quem roda o teste).
    padrao = {nome: campo.default for nome, campo in Settings.model_fields.items()}
    assert padrao["atendimento_agente_ativo"] is True
    assert padrao["atendimento_agente_api_key"] == ""
    # Item 5 da rodada "redondo" (23/10/2026): o Sonnet 5.5, o prazo de 60 s e os tetos
    # de US$ 10 por dia e US$ 0,50 por conversa (o verificador continua no Haiku).
    assert padrao["atendimento_agente_modelo"] == "claude-sonnet-5-5"
    assert padrao["atendimento_agente_effort"] == "medium"
    assert padrao["atendimento_agente_max_turnos"] == 5
    assert padrao["atendimento_agente_prazo_s"] == 60
    assert padrao["atendimento_agente_teto_usd_dia"] == 10.0
    assert padrao["atendimento_agente_teto_usd_conversa"] == 0.50
    assert padrao["atendimento_agente_replay"] is False
    assert padrao["atendimento_agente_replay_plataformas"] == "shopee"
    assert padrao["atendimento_sites_marca_ativo"] is True
    # A lista de canais NÃO existe: quem liga é a coluna do canal.
    assert not any(n.startswith("atendimento_agente_canais") for n in padrao)
    # Com o padrão e sem chave nenhuma, o agente não roda em canal nenhum.
    s = SimpleNamespace(**padrao)
    assert agente_config.chave_do_agente(s) is None
    assert agente_config.agente_ligado(s, SimpleNamespace(agente=True)) is False


# ─────────────── correção do "entregue" ───────────────


@pytest.mark.parametrize(
    ("localizacao", "esperado"),
    [
        ("Pedido entregue", True),
        ("Entregue", True),
        ("ENTREGUE → São Paulo/SP", True),
        ("Entregue → Rio de Janeiro/RJ · previsão 16/07", True),
        ("Entregue longe do endereço (aguarda confirmação)", True),
        # A loja entregou na agência (o `dropped_off` do ML): ainda não chegou.
        ("Entregue à agência → Piracicaba/SP", False),
        ("Pedido entregue ao remetente", False),
        ("Entregue ao remetente", False),
        ("Entregue — devolvido ao vendedor", False),
        ("Pedido será devolvido ao vendedor", False),
        ("Devolução não entregue", False),
        ("Não entregue", False),
        ("Pedido postado Piracicaba", False),
        ("São Paulo/SP - Objeto entregue ao destinatário", False),
        ("Entregues ao centro", False),
        ("", False),
        (None, False),
    ],
)
def test_localizacao_diz_entregue(localizacao, esperado):
    assert contexto.localizacao_diz_entregue(localizacao) is esperado


def test_entregue_da_logistica():
    assert contexto.entregue_da_logistica(None) is False
    assert contexto.entregue_da_logistica({}) is False
    assert contexto.entregue_da_logistica({"entregue_em": "2026-10-19T10:00:00+00:00"}) is True
    assert contexto.entregue_da_logistica({"entregue_em": None, "status": "Pedido entregue"})
    assert not contexto.entregue_da_logistica(
        {"entregue_em": None, "status": "Entregue à agência → Piracicaba/SP"}
    )
    # Com ocorrência na transportadora o status é a ocorrência: só a data vale.
    assert not contexto.entregue_da_logistica(
        {"entregue_em": None, "status": "ocorrência na transportadora: extraviado"}
    )


async def test_logistica_do_contexto_com_a_correcao(db: AsyncSession):
    db.add_all(
        [
            Logistica(
                pedido_bling="9100", pedido_marketplace="MKT-ENT", localizacao="Pedido entregue"
            ),
            Logistica(
                pedido_bling="9101",
                pedido_marketplace="MKT-OCO",
                localizacao="Entregue → São Paulo/SP",
                problema_correios="objeto extraviado",
            ),
            Logistica(
                pedido_bling="9102",
                pedido_marketplace="MKT-AGE",
                localizacao="Entregue à agência → Piracicaba/SP",
            ),
        ]
    )
    await db.commit()
    entregue = await contexto._logistica(db, "9100", "MKT-ENT")
    # O dicionário não muda de forma (a tela e os testes do legado leem estas chaves).
    assert set(entregue) == {
        "rastreio",
        "transportadora",
        "previsao",
        "entregue_em",
        "status",
        "data_envio",
    }
    assert entregue["entregue_em"] is None
    assert contexto.entregue_da_logistica(entregue) is True
    assert contexto.entregue_da_logistica(await contexto._logistica(db, "9101", "MKT-OCO")) is False
    assert contexto.entregue_da_logistica(await contexto._logistica(db, "9102", "MKT-AGE")) is False


# ─────────────── constantes e manual ───────────────


def _manual_base() -> dict:
    return json.loads((_API / "scripts" / "atendimento_manual_base.json").read_text())


def test_assuntos_de_orientacao_sao_os_14_menos_os_dois_de_pessoa():
    so_humano = {c["id"] for c in _manual_base()["categorias"] if c["so_humano"]}
    assert len(so_humano) == 14
    assert len(constantes.ASSUNTOS_ORIENTACAO) == 12
    assert len(set(constantes.ASSUNTOS_ORIENTACAO)) == 12
    assert set(constantes.ASSUNTOS_ORIENTACAO) == so_humano - {
        "reclamacao_forte",
        "marca_autorizacao",
    }
    # O legado não muda.
    assert constantes.CATEGORIAS_SO_HUMANO == (
        "troca_devolucao",
        "cancelamento",
        "defeito",
        "reembolso",
        "garantia",
        "endereco",
        "desconto",
        "reclamacao_forte",
    )


def test_acoes_de_pessoa_na_ordem_da_regua():
    assert constantes.ACOES_PESSOA == (
        "ag_cancelamento",
        "juridico_fraude_ofensa",
        "reclamacao_aberta",
        "avaliacao_baixa",
        "instrucao",
        "pediu_pessoa",
        "segurar_envio",
        "alterar_endereco",
        "enviar_arquivo_nf",
        "analisar_evidencia",
        "abrir_chamado",
        "aprovar_fora_da_plataforma",
        "desconto_brinde",
        "excecao",
        "dado_indisponivel",
    )
    assert set(constantes.ROTULO_ACAO_PESSOA) == set(constantes.ACOES_PESSOA)
    assert constantes.rotulo_acao_pessoa("excecao") == "Exceção à política"
    assert constantes.rotulo_acao_pessoa("novo") == "novo"
    # Cabe no `prompt_versao` do rascunho (String(16)).
    assert constantes.PROMPT_VERSAO_AGENTE == "ag1"


def test_escopos_e_fontes_batem_com_os_checks_da_tabela():
    checks = {
        c.name: str(c.sqltext)
        for c in AtendimentoFato.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
    }

    def valores(nome: str) -> tuple[str, ...]:
        return tuple(re.findall(r"'([a-z_]+)'", checks[nome]))

    assert valores("ck_atendimento_fatos_escopo") == constantes.FATO_ESCOPOS
    assert valores("ck_atendimento_fatos_fonte") == constantes.FATO_FONTES
    assert len(set(constantes.FONTES_PAR)) == 5


def test_cobertura_regiao_com_o_texto_de_20_10():
    regras = [r for r in _manual_base()["regras"] if r["categoria"] == "cobertura_regiao"]
    assert len(regras) == 1
    assert regras[0]["faca"] == (
        "Se a descrição ou os itens do anúncio nos FATOS dizem que não há envio para o "
        "estado ou a cidade do cliente: responder sozinho (precisa_humano=false), citando o "
        "anúncio — ex.: 'Conforme informado no anúncio, não realizamos envios para o Rio de "
        "Janeiro.' — e sugerir conferir pelo CEP no anúncio. Nos outros casos: "
        "precisa_humano=true; sugestão: a disponibilidade para o CEP aparece no anúncio ao "
        "informar o CEP. Nunca citar outro anúncio."
    )
