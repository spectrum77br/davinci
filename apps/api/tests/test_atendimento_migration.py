"""As migrations 0346…0430 do atendimento criam EXATAMENTE o que o model declara (e desfazem).

O conftest monta o schema de teste pelo `create_all` do model, nunca pela
migration. Sem este teste, uma coluna esquecida na 0346 (ou um índice parcial
com outro predicado) só apareceria no deploy, em produção. Aqui a migration
roda de verdade num schema descartável e o catálogo do Postgres dos dois
lados é comparado: colunas (tipo, tamanho, nulo, default), constraints (PK,
FK com ON DELETE, UNIQUE) e índices (inclusive os parciais).

A 0347 (30/09/2026, lojas do robô do Mac mini: Temu e AliExpress) mexe nas
mesmas tabelas: roda em cima da 0346, e o catálogo que se compara com o model
é o das duas juntas.

A 0353 (01/10/2026, etiqueta = status atual e reclamações da plataforma) põe
as colunas da etiqueta na conversa e cria `atendimento_etiquetas_historico` e
`atendimento_reclamacoes`. Roda em cima das duas (as 0348–0352 do meio são da
denúncia e não tocam em `atendimento_*`), e o downgrade DELA SÓ tem de voltar
o catálogo exatamente ao que era depois da 0347.

A 0358 (02/10/2026, avaliações de venda — RF8) estende
`atendimento_avaliacoes_loja` (mídia, resposta, pendência, conversa, ML) e
roda em cima das três (as 0354–0357 do meio são da denúncia e do Carrefour); o downgrade
dela volta o catálogo ao de depois da 0353. A linha que já existia (Shopee)
ganha `pode_responder = true`.

A 0362 (02/10/2026, carrinho abandonado dos sites e comentários das redes —
RF9 e RF7) dá ao canal a origem EXTERNA (`externo_ref`, `rede_social_id`, o
CHECK `tem_origem` no lugar do `integracao_ou_robo`) e cria
`atendimento_carrinhos`, `atendimento_publicacoes` e
`atendimento_comentarios`. Roda em cima das quatro (as 0359–0361 do meio são
de lojas, da Netshoes e da agenda do robô de denúncia); o canal que já
existia passa no CHECK novo, o externo nasce sem integração nem robô, e o
downgrade dela volta o catálogo ao
de depois da 0358 (apagando o canal externo; a conversa fica, sem canal).

A 0366 (05/10/2026, mensagens automáticas — docs/atendimento-automacoes.md)
cria `atendimento_automacao_regras` e `atendimento_automacao_registros` e
SEMEIA as regras do catálogo em cada loja Shopee/TikTok/ML ativa: `simular`
onde o Duoke manda hoje (pelo nome da integração), `desligado` no resto.
Roda em cima das cinco (as 0363–0365 do meio são da denúncia e dos e-mails da
marca, sem `atendimento_*`); o downgrade dela volta o catálogo ao de depois da
0362. A semente da 0366 ficou CONGELADA nas automações daquela data
(`CODIGOS`): as que o catálogo ganhou em 05/10 à noite (pedido não pago,
resposta da avaliação, "pedido recebido" do TikTok — todas só simulação) são
semeadas pela 0371, que só insere regras (nenhuma tabela) e cujo downgrade
apaga só as dela.

A 0379 (07/10/2026, troca de produto — item 4, fase 4c) cria
`atendimento_trocas`: CHECK do estado e da fonte do aceite, UNIQUE da
`idem_key` e o índice único PARCIAL de uma troca aberta por pedido. Roda em
cima de todas (as 0372–0378 do meio não tocam em `atendimento_*`); o
downgrade dela volta o catálogo ao de depois da 0371.

A 0396 (14/10/2026, o "Falta responder" igual ao Duoke) põe na conversa a
`ultima_loja_qualquer_em` (anulável, sem índice) e a preenche pelas
mensagens; roda em cima de todas (as 0384–0395 do meio não tocam no catálogo
de `atendimento_*`) e o downgrade dela volta o catálogo ao de depois da 0383.

A 0399 (15/10/2026, o número da Caixa = o "não lida" do Duoke) põe na conversa
o `visto_em` ("visto no DaVinci": anulável, sem índice e sem preenchimento);
roda em cima de todas (as 0397–0398 do meio são da conciliação) e o downgrade
dela volta o catálogo ao de depois da 0396.

A 0416 (o estado inicial das notificações nativas: `notificacoes_migracao` no
canal; `notificacoes_corte_em`, `notificacoes_iniciais` e
`notificacoes_visto_em` na conversa) e a 0420 (20/10/2026, o AGENTE de
atendimento: `agente` e `auto_orientacao` no canal) rodam em cima de todas;
os downgrades voltam o catálogo ao de antes de cada uma. As 4 tabelas novas
da 0420 (fatos, pares, replay e o instantâneo dos sites da marca) são
conferidas em `test_atendimento_agente_esquema.py`, com a semente.

A 0430 (19/10/2026, o canal Zap — WhatsApp Cloud API, docs/zap/desenho.md
§4.2) cria as seis `atendimento_zap_*` (app, WABA, número, contato, eventos
do webhook e mídia): CHECKs das colunas de estado, os UNIQUE parciais do
contato (bsuid e wa_id_norm) e o índice parcial da fila de eventos. A FK
`numeros.marca_id` exige o stub `marcas` no rascunho. Roda em cima de todas e
o downgrade dela volta o catálogo ao de depois da 0416.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Base
from app.models import atendimento_zap as zap_model
from app.services.atendimento import automacoes_catalogo as cat

_VERSOES = Path(__file__).resolve().parent.parent / "alembic" / "versions"
_MIGRATION = _VERSOES / "0346_atendimento.py"
_MIGRATION_ROBO = _VERSOES / "0347_atendimento_robo.py"
_MIGRATION_ETIQUETAS = _VERSOES / "0353_atendimento_etiquetas.py"
_MIGRATION_AVALIACOES = _VERSOES / "0358_atendimento_avaliacoes.py"
_MIGRATION_CARRINHO_REDES = _VERSOES / "0362_atendimento_carrinho_redes.py"
_MIGRATION_AUTOMACOES = _VERSOES / "0366_atendimento_automacoes.py"
_MIGRATION_SIMULACAO = _VERSOES / "0371_atendimento_automacoes_simulacao.py"
_MIGRATION_TROCAS = _VERSOES / "0383_atendimento_trocas.py"
_MIGRATION_FALTA = _VERSOES / "0396_ultima_loja_qualquer.py"
_MIGRATION_VISTO = _VERSOES / "0399_atendimento_visto_em.py"
_MIGRATION_NOTIFICACOES = _VERSOES / "0416_atendimento_notificacoes_nativas.py"
_MIGRATION_AGENTE = _VERSOES / "0420_atendimento_agente.py"
_MIGRATION_ZAP = _VERSOES / "0430_atendimento_zap.py"
# A regra de palavras das pastas de e-mail (0387, a camada do atendimento sobre
# a Central de e-mail) é conferida em `test_mail_atendimento_migration.py`,
# junto das tabelas `mail_*` da mesma migration. As tabelas novas da 0420 (o
# agente), em `test_atendimento_agente_esquema.py`.
DE_OUTRA_MIGRATION = frozenset(
    {
        "atendimento_regras_pasta_email",
        "atendimento_fatos",
        "atendimento_pares",
        "atendimento_agente_replay",
        "atendimento_site_produtos",
    }
)
TABELAS = sorted(
    t.name
    for t in Base.metadata.sorted_tables
    if t.name.startswith("atendimento_") and t.name not in DE_OUTRA_MIGRATION
)


def _carregar_migration(caminho: Path = _MIGRATION):
    spec = importlib.util.spec_from_file_location(f"migration_{caminho.stem}", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db: AsyncSession, schema: str, *remover: str) -> dict[str, list]:
    """Colunas, constraints e índices das tabelas atendimento_* de um schema."""

    def limpo(s: str | None) -> str | None:
        for prefixo in remover:
            s = s.replace(f"{prefixo}.", "") if s else s
        return s

    colunas = (
        await db.execute(
            text(
                """
                SELECT table_name, column_name, udt_name, character_maximum_length,
                       is_nullable, column_default
                  FROM information_schema.columns
                 WHERE table_schema = :schema AND table_name LIKE 'atendimento\\_%'
                   AND NOT (table_name = ANY(:fora))
                 ORDER BY table_name, column_name
                """
            ),
            {"schema": schema, "fora": sorted(DE_OUTRA_MIGRATION)},
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
                 WHERE n.nspname = :schema AND cl.relname LIKE 'atendimento\\_%'
                   AND NOT (cl.relname = ANY(:fora))
                 ORDER BY cl.relname, co.conname
                """
            ),
            {"schema": schema, "fora": sorted(DE_OUTRA_MIGRATION)},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                """
                SELECT tablename, indexname, indexdef
                  FROM pg_indexes
                 WHERE schemaname = :schema AND tablename LIKE 'atendimento\\_%'
                   AND NOT (tablename = ANY(:fora))
                 ORDER BY tablename, indexname
                """
            ),
            {"schema": schema, "fora": sorted(DE_OUTRA_MIGRATION)},
        )
    ).all()
    return {
        "tabelas": sorted({r[0] for r in colunas}),
        "colunas": [tuple(r[:5]) + (limpo(r[5]),) for r in colunas],
        "constraints": [(r[0], r[1], r[2], limpo(r[3])) for r in constraints],
        "indices": [(r[0], r[1], limpo(r[2])) for r in indices],
    }


@pytest.mark.asyncio
async def test_migration_bate_com_o_model_e_o_downgrade_desfaz(db: AsyncSession):
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0346"
    mod = _carregar_migration()
    assert mod.revision == "0346_atendimento"
    assert mod.down_revision == "0345_denuncia_acesso_cairo"
    robo = _carregar_migration(_MIGRATION_ROBO)
    assert robo.revision == "0347_atendimento_robo"
    assert robo.down_revision == "0346_atendimento"
    etiq = _carregar_migration(_MIGRATION_ETIQUETAS)
    assert etiq.revision == "0353_atendimento_etiquetas"
    assert etiq.down_revision == "0352_nf_command_urgente"
    aval = _carregar_migration(_MIGRATION_AVALIACOES)
    assert aval.revision == "0358_atendimento_avaliacoes"
    # Depois do último head do origin quando foi escrita (0354–0356: denúncia;
    # 0357: Carrefour).
    assert aval.down_revision == "0357_marketplace_carrefour"
    externo = _carregar_migration(_MIGRATION_CARRINHO_REDES)
    assert externo.revision == "0362_atendimento_carrinho_redes"
    # Depois do último head do origin (0359: lojas; 0360: Netshoes; 0361:
    # agenda do robô de denúncia). Esta nasceu 0361 e foi renumerada para
    # 0362 em 02/10, quando a da denúncia chegou ao origin primeiro.
    assert externo.down_revision == "0361_denuncia_robo_agenda"
    automacoes = _carregar_migration(_MIGRATION_AUTOMACOES)
    assert automacoes.revision == "0366_atendimento_automacoes"
    # Depois do último head do origin (0364: relatórios da denúncia; 0365:
    # e-mails da marca). Esta nasceu 0364 e foi renumerada para 0366 em 05/10,
    # quando as duas chegaram ao origin primeiro.
    assert automacoes.down_revision == "0365_marca_emails"
    simulacao = _carregar_migration(_MIGRATION_SIMULACAO)
    assert simulacao.revision == "0371_atendimento_automacoes_simulacao"
    # Depois do último head do origin em 05/10 à noite (0367: flex; 0368–0370: denúncia).
    assert simulacao.down_revision == "0370_denuncia_robo_agenda_replica_19h"
    trocas = _carregar_migration(_MIGRATION_TROCAS)
    assert trocas.revision == "0383_atendimento_trocas"
    # Nasceu 0379 em 07/10 e foi renumerada para 0383 em 08/10, quando o origin
    # chegou primeiro com 0379–0382 (preços, garantias, anúncios e conferência).
    assert trocas.down_revision == "0382_conferencia_plataformas"
    falta = _carregar_migration(_MIGRATION_FALTA)
    assert falta.revision == "0396_ultima_loja_qualquer"
    assert falta.down_revision == "0395_cairo_tabela_precos_contas"
    visto = _carregar_migration(_MIGRATION_VISTO)
    assert visto.revision == "0399_atendimento_visto_em"
    assert visto.down_revision == "0398_conciliacao_permissao"
    notificacoes = _carregar_migration(_MIGRATION_NOTIFICACOES)
    assert notificacoes.revision == "0416_notificacoes_nativas"
    assert notificacoes.down_revision == "0415_mail_admin"
    agente = _carregar_migration(_MIGRATION_AGENTE)
    assert agente.revision == "0420_atendimento_agente"
    assert agente.down_revision == "0419_londres_logistica_editar"
    zap = _carregar_migration(_MIGRATION_ZAP)
    assert zap.revision == "0430_atendimento_zap"
    # Nasceu 0415 no desenho (17/10), virou 0417 em 19/10 e 0430 em 26/10:
    # o origin chegou primeiro com as 0415–0429.
    assert zap.down_revision == "0429_compra_contabilidade"
    # Os valores dos CHECKs da migration são cópia dos do model.
    for na_migration, no_model in (
        (zap.CONTA_STATUS, zap_model.ZAP_CONTA_STATUS),
        (zap.NUMERO_ETAPAS, zap_model.ZAP_NUMERO_ETAPAS),
        (zap.NUMERO_SAUDE, zap_model.ZAP_NUMERO_SAUDE),
        (zap.OPT_OUT_TIPOS, zap_model.ZAP_OPT_OUT_TIPOS),
        (zap.EVENTO_TIPOS, zap_model.ZAP_EVENTO_TIPOS),
        (zap.EVENTO_RESULTADOS, zap_model.ZAP_EVENTO_RESULTADOS),
        (zap.MIDIA_DIRECOES, zap_model.ZAP_MIDIA_DIRECOES),
        (zap.MIDIA_STATUS, zap_model.ZAP_MIDIA_STATUS),
    ):
        assert na_migration == no_model
    # 7 da primeira parte + 3 da parte 2 (categorias e os índices do cartão
    # "Cliente") + 2 da 0353 (histórico da etiqueta e reclamações) + 3 da
    # 0362 (carrinhos, publicações e comentários) + 2 da 0366 (regras e
    # registro das mensagens automáticas) + 1 da 0383 (trocas de produto) +
    # 6 da 0430 (o Zap).
    assert len(TABELAS) == 24
    assert {
        "atendimento_categorias",
        "atendimento_pedidos_comprador",
        "atendimento_avaliacoes_loja",
        "atendimento_etiquetas_historico",
        "atendimento_reclamacoes",
        "atendimento_carrinhos",
        "atendimento_publicacoes",
        "atendimento_comentarios",
        "atendimento_automacao_regras",
        "atendimento_automacao_registros",
        "atendimento_trocas",
        "atendimento_zap_apps",
        "atendimento_zap_contas",
        "atendimento_zap_numeros",
        "atendimento_zap_contatos",
        "atendimento_zap_eventos",
        "atendimento_zap_midias",
    } <= set(TABELAS)

    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que as FKs da 0346 referenciam.
    await db.execute(text(f'CREATE TABLE "{rascunho}".users (id uuid PRIMARY KEY)'))
    # A 0366 semeia as regras pelo nome e pela plataforma da integração.
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".integrations (id uuid PRIMARY KEY, name text, '
            "platform text, archived_at timestamptz)"
        )
    )
    # A 0353 põe índice em `bling_orders.numeroloja` (o elo conversa → pedido).
    await db.execute(
        text(f'CREATE TABLE "{rascunho}".bling_orders (id uuid PRIMARY KEY, numeroloja text)')
    )
    # A 0362 liga o canal de rede à conta do cadastro (`redes_sociais`).
    await db.execute(text(f'CREATE TABLE "{rascunho}".redes_sociais (id uuid PRIMARY KEY)'))
    # A 0420 liga o fato ao produto (FK) e só semeia o de produto que existe.
    await db.execute(text(f'CREATE TABLE "{rascunho}".products (id uuid PRIMARY KEY)'))
    # A 0430 liga o número do Zap à marca (`atendimento_zap_numeros.marca_id`).
    await db.execute(text(f'CREATE TABLE "{rascunho}".marcas (id uuid PRIMARY KEY)'))
    await db.commit()

    def _rodar(conn, passo: str, migrations) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        # Quem chama passa na ordem certa: a subida 0346 → 0347 → 0353, a
        # descida ao contrário.
        with Operations.context(ctx):
            for m in migrations:
                getattr(m, passo)()

    mod.SCHEMA = rascunho
    robo.SCHEMA = rascunho
    etiq.SCHEMA = rascunho
    aval.SCHEMA = rascunho
    externo.SCHEMA = rascunho
    automacoes.SCHEMA = rascunho
    simulacao.SCHEMA = rascunho
    trocas.SCHEMA = rascunho
    falta.SCHEMA = rascunho
    visto.SCHEMA = rascunho
    notificacoes.SCHEMA = rascunho
    agente.SCHEMA = rascunho
    zap.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade", (mod, robo))
        await db.commit()
        antes_da_0353 = await _catalogo(db, rascunho, rascunho)
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade", (etiq,))
        await db.commit()
        antes_da_0358 = await _catalogo(db, rascunho, rascunho)
        # Uma avaliação de antes (Shopee, do job de hora em hora): a 0358 dá a
        # ela o `pode_responder` (a Shopee responde pela API).
        integ = "00000000-0000-0000-0000-000000000001"
        await db.execute(
            text(f'INSERT INTO "{rascunho}".integrations (id) VALUES (:i)'), {"i": integ}
        )
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".atendimento_avaliacoes_loja '
                "(id, integration_id, plataforma, comentario_id, estrelas) "
                "VALUES (gen_random_uuid(), :i, 'shopee', '1', 5)"
            ),
            {"i": integ},
        )
        await db.commit()
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade", (aval,))
        await db.commit()
        linha = (
            await db.execute(
                text(
                    f'SELECT pode_responder, midia, dados, pendente_desde FROM "{rascunho}"'
                    ".atendimento_avaliacoes_loja"
                )
            )
        ).one()
        assert tuple(linha) == (True, [], {}, None)
        await db.execute(text(f'DELETE FROM "{rascunho}".atendimento_avaliacoes_loja'))
        await db.execute(text(f'DELETE FROM "{rascunho}".integrations'))
        await db.commit()
        antes_da_0362 = await _catalogo(db, rascunho, rascunho)

        # Um canal de antes (loja do robô): passa no CHECK novo da 0362.
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".atendimento_canais (id, robo_perfil_id, plataforma, '
                "canal) VALUES (gen_random_uuid(), 'k1dkegpc', 'temu', 'chat')"
            )
        )
        await db.commit()
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade", (externo,))
        await db.commit()
        # O canal EXTERNO (site) nasce sem integração nem robô; sem origem
        # nenhuma, o CHECK recusa.
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".atendimento_canais (id, externo_ref, plataforma, '
                "canal) VALUES (gen_random_uuid(), 'site:charlots', 'site', 'carrinho')"
            )
        )
        await db.commit()
        with pytest.raises(Exception, match="ck_atendimento_canais_tem_origem"):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".atendimento_canais (id, plataforma, canal) '
                    "VALUES (gen_random_uuid(), 'site', 'carrinho')"
                )
            )
        await db.rollback()
        # A conversa do canal externo (para o downgrade: ela fica, sem canal).
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".atendimento_conversas (id, canal_id, plataforma, '
                "canal, externo_id) SELECT gen_random_uuid(), id, 'site', 'carrinho', "
                f"'lojista:1' FROM \"{rascunho}\".atendimento_canais "
                "WHERE externo_ref IS NOT NULL"
            )
        )
        await db.commit()
        antes_da_0366 = await _catalogo(db, rascunho, rascunho)

        # Lojas para a semente da 0366: a Barbosa (Shopee, o Duoke manda o
        # menu), a Aguiar (Shopee, não manda), a ATV (só o "aguarde" e as
        # campanhas), a Mini do TikTok, a Inova (mala: o entregue de mala), a
        # Bling (fora) e uma Shopee arquivada (fora).
        lojas = {
            "barbosa": ("00000000-0000-0000-0000-0000000000b1", "shopee", None),
            "aguiar": ("00000000-0000-0000-0000-0000000000a1", "shopee", None),
            "atv": ("00000000-0000-0000-0000-0000000000a2", "shopee", None),
            "inova": ("00000000-0000-0000-0000-0000000000a3", "shopee", None),
            "mini": ("00000000-0000-0000-0000-0000000000c1", "tiktok", None),
            "bling": ("00000000-0000-0000-0000-0000000000d1", "bling", None),
            "velha": (
                "00000000-0000-0000-0000-0000000000e1",
                "shopee",
                datetime(2026, 1, 1, tzinfo=UTC),
            ),
        }
        for nome, (i, plat, arq) in lojas.items():
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".integrations (id, name, platform, archived_at) '
                    "VALUES (CAST(:i AS uuid), :n, :p, :a)"
                ),
                {
                    "i": i,
                    "n": f" {nome.upper()} " if nome == "barbosa" else nome,
                    "p": plat,
                    "a": arq,
                },
            )
        await db.commit()
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade", (automacoes,))
        await db.commit()
        semente = {
            (r[0], r[1]): (r[2], r[3], r[4])
            for r in (
                await db.execute(
                    text(
                        "SELECT i.name, r.automacao, r.modo, r.ligada_desde IS NOT NULL, "
                        "r.partes::text FROM "
                        f'"{rascunho}".atendimento_automacao_regras r '
                        f'JOIN "{rascunho}".integrations i ON i.id = r.integration_id'
                    )
                )
            ).all()
        }
        barbosa = " BARBOSA "
        assert semente[(barbosa, "shopee_menu")][:2] == ("simular", True)
        assert semente[(barbosa, "shopee_opcao_4")][:2] == ("desligado", False)
        assert semente[(barbosa, "shopee_aguarde")][0] == "desligado"
        assert semente[("aguiar", "shopee_menu")][0] == "desligado"
        assert semente[("atv", "shopee_aguarde")][0] == "simular"
        assert semente[("atv", "shopee_menu")][0] == "desligado"
        assert semente[("atv", "shopee_pedido_recebido")][0] == "simular"
        assert semente[("mini", "tiktok_convite")][0] == "simular"
        # O entregue com o texto do tipo da loja (celular × mala), nome dentro.
        assert "Confirmamos a entrega" in semente[(barbosa, "shopee_entregue")][2]
        assert "sua mala já chegou" in semente[("inova", "shopee_entregue")][2]
        assert not any(nome in ("bling", "velha") for nome, _ in semente)
        # Uma regra por automação da plataforma em cada loja — as da 0366 (a
        # semente congelada: nada das automações que vieram depois).
        assert sum(1 for nome, _ in semente if nome == "mini") == 4
        assert not any(c in simulacao.CODIGOS for _, c in semente)
        assert set(automacoes.CODIGOS) | set(simulacao.CODIGOS) == set(cat.CATALOGO)

        # A 0371 semeia as que faltavam (só simulação), sem tocar no que existe.
        await db.execute(
            text(
                f"UPDATE \"{rascunho}\".atendimento_automacao_regras SET modo = 'enviar' "
                "WHERE automacao = 'shopee_menu'"
            )
        )
        await db.commit()
        catalogo_antes_da_0371 = await _catalogo(db, rascunho, rascunho)
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade", (simulacao,))
        await db.commit()
        assert await _catalogo(db, rascunho, rascunho) == catalogo_antes_da_0371, "só dado"
        semente2 = {
            (r[0], r[1]): (r[2], r[3], r[4])
            for r in (
                await db.execute(
                    text(
                        "SELECT i.name, r.automacao, r.modo, r.ligada_desde IS NOT NULL, "
                        "r.partes::text FROM "
                        f'"{rascunho}".atendimento_automacao_regras r '
                        f'JOIN "{rascunho}".integrations i ON i.id = r.integration_id'
                    )
                )
            ).all()
        }
        assert semente2[(barbosa, "shopee_menu")][0] == "enviar", "a regra que já existia fica"
        assert semente2[(barbosa, "shopee_nao_pago")][:2] == ("simular", True)
        assert "R${valor_cupom} OFF" in semente2[(barbosa, "shopee_nao_pago")][2]
        assert semente2[("inova", "shopee_nao_pago")][0] == "simular"
        assert semente2[("aguiar", "shopee_nao_pago")][0] == "desligado"
        assert semente2[("atv", "shopee_avaliacao_boa")][0] == "simular"
        assert semente2[("atv", "shopee_avaliacao_ruim")][0] == "simular"
        assert '"resposta_publica"' in semente2[("atv", "shopee_avaliacao_boa")][2]
        assert semente2[("aguiar", "shopee_avaliacao_boa")][0] == "desligado"
        assert semente2[("mini", "tiktok_pedido_recebido")][:2] == ("simular", True)
        assert sum(1 for nome, _ in semente2 if nome == "mini") == 5
        assert not any(nome in ("bling", "velha") for nome, _ in semente2)
        # Idempotente: rodar de novo não duplica nem muda.
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade", (simulacao,))
        await db.commit()
        assert (
            await db.scalar(text(f'SELECT count(*) FROM "{rascunho}".atendimento_automacao_regras'))
        ) == len(semente2)
        # O downgrade da 0371 apaga só as dela.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", (simulacao,))
        await db.commit()
        restantes = {
            r[0]
            for r in (
                await db.execute(
                    text(f'SELECT automacao FROM "{rascunho}".atendimento_automacao_regras')
                )
            ).all()
        }
        assert restantes == {c for _, c in semente}
        await db.execute(text(f'DELETE FROM "{rascunho}".atendimento_automacao_regras'))
        await db.execute(text(f'DELETE FROM "{rascunho}".integrations'))
        await db.commit()

        # 0379: a troca de produto.
        antes_da_0379 = await _catalogo(db, rascunho, rascunho)
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade", (trocas,))
        await db.commit()

        # 0396: a coluna do "Falta responder" igual ao Duoke.
        antes_da_0396 = await _catalogo(db, rascunho, rascunho)
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade", (falta,))
        await db.commit()

        # 0399: o "visto no DaVinci" (o número da Caixa igual ao Duoke).
        antes_da_0399 = await _catalogo(db, rascunho, rascunho)
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade", (visto,))
        await db.commit()

        # 0416: o estado inicial das notificações nativas (canal e conversa).
        antes_da_0416 = await _catalogo(db, rascunho, rascunho)
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade", (notificacoes,))
        await db.commit()

        # 0420: o botão "Agente (Claude)" e os assuntos de orientação do canal.
        antes_da_0420 = await _catalogo(db, rascunho, rascunho)
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade", (agente,))
        await db.commit()

        # 0430: o canal Zap (as seis `atendimento_zap_*`).
        antes_da_0430 = await _catalogo(db, rascunho, rascunho)
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade", (zap,))
        await db.commit()

        da_migration = await _catalogo(db, rascunho, rascunho)
        do_model = await _catalogo(db, schema_model, schema_model)
        assert da_migration["tabelas"] == TABELAS
        # Comparação que não compara nada passaria calada: o catálogo tem corpo.
        assert len(da_migration["colunas"]) > 80
        nomes = {c[1] for c in da_migration["constraints"]}
        assert {
            "pk_atendimento_canais",
            "uq_atendimento_canais_integration_id_canal",
            "uq_atendimento_conversas_integration_id_canal_externo_id",
            "uq_atendimento_mensagens_conversa_id_externo_id",
            "uq_atendimento_avaliacoes_rascunho_id",
            "fk_atendimento_rascunhos_gatilho_atendimento_mensagens",
            "fk_atendimento_conversas_canal_id_atendimento_canais",
            "pk_atendimento_categorias",
            "uq_atendimento_pedidos_comprador_integration_id_pedido",
            "fk_atendimento_pedidos_comprador_integration_id_integrations",
            "uq_atendimento_avaliacoes_loja_integration_id_comentario_id",
            "fk_atendimento_avaliacoes_loja_integration_id_integrations",
            # 0347: o canal do robô (perfil do AdsPower, sem integração). O
            # CHECK dela (`integracao_ou_robo`) a 0362 troca pelo `tem_origem`.
            "uq_atendimento_canais_robo_perfil_id",
            # 0353: histórico da etiqueta (FK com nome à mão) e reclamações.
            "pk_atendimento_etiquetas_historico",
            "fk_atendimento_etiquetas_historico_conversa",
            "fk_atendimento_etiquetas_historico_por_user_id_users",
            "pk_atendimento_reclamacoes",
            "uq_atendimento_reclamacoes_plataforma_externo_id",
            "fk_atendimento_reclamacoes_conversa_id_atendimento_conversas",
            "fk_atendimento_reclamacoes_integration_id_integrations",
            # 0358: a avaliação ligada à conversa (FK com nome à mão) e quem tratou.
            "fk_atendimento_avaliacoes_loja_conversa",
            "fk_atendimento_avaliacoes_loja_tratada_por_users",
            # 0362: o canal externo (CHECK novo no lugar do da 0347), o
            # carrinho, a publicação e o comentário (FK com nome à mão).
            "ck_atendimento_canais_tem_origem",
            "uq_atendimento_canais_externo_ref_canal",
            "fk_atendimento_canais_rede_social_id_redes_sociais",
            "pk_atendimento_carrinhos",
            "fk_atendimento_carrinhos_conversa_id_atendimento_conversas",
            "fk_atendimento_carrinhos_tratado_por_users",
            "uq_atendimento_publicacoes_plataforma_conta_id_externo_id",
            "uq_atendimento_comentarios_plataforma_externo_id",
            "fk_atendimento_comentarios_publicacao",
            # 0366: regras (uma por automação × loja) e o registro (a chave
            # única, as FKs com nome à mão e o CHECK do estado).
            "uq_atendimento_automacao_regras_automacao_integration_id",
            "ck_atendimento_automacao_regras_modo",
            "uq_atendimento_automacao_registros_chave",
            "ck_atendimento_automacao_registros_estado",
            "fk_atendimento_automacao_registros_regra",
            "fk_atendimento_automacao_registros_conversa",
            "fk_atendimento_automacao_registros_gatilho",
            "fk_atendimento_automacao_registros_duoke",
            # 0379: a troca (CHECKs do estado e do aceite, a idem_key, a FK
            # da mensagem do aceite com nome à mão).
            "pk_atendimento_trocas",
            "ck_atendimento_trocas_estado",
            "ck_atendimento_trocas_aceite_fonte",
            "uq_atendimento_trocas_idem_key",
            "fk_atendimento_trocas_conversa_id_atendimento_conversas",
            "fk_atendimento_trocas_mensagem_aceite",
            "fk_atendimento_trocas_criado_por_users",
            # 0430: o Zap — os UNIQUE da chave de cada nível (app_id, waba_id,
            # phone_number_id, a chave do evento, a mídia por número), os
            # CHECKs das colunas de estado e as FKs (a do contato com nome à mão).
            "pk_atendimento_zap_apps",
            "uq_atendimento_zap_apps_app_id",
            "fk_atendimento_zap_apps_criado_por_users",
            "uq_atendimento_zap_contas_waba_id",
            "ck_atendimento_zap_contas_status",
            "fk_atendimento_zap_contas_app_ref_atendimento_zap_apps",
            "uq_atendimento_zap_numeros_phone_number_id",
            "uq_atendimento_zap_numeros_canal_id",
            "ck_atendimento_zap_numeros_etapa",
            "ck_atendimento_zap_numeros_saude_status",
            "fk_atendimento_zap_numeros_conta_ref_atendimento_zap_contas",
            "fk_atendimento_zap_numeros_marca_id_marcas",
            "fk_atendimento_zap_numeros_canal_id_atendimento_canais",
            "uq_atendimento_zap_contatos_conversa_id",
            "ck_atendimento_zap_contatos_opt_out_tipo",
            "fk_atendimento_zap_contatos_numero",
            "fk_atendimento_zap_contatos_conversa_id_atendimento_conversas",
            "uq_atendimento_zap_eventos_chave",
            "ck_atendimento_zap_eventos_tipo",
            "ck_atendimento_zap_eventos_resultado",
            "fk_atendimento_zap_eventos_app_ref_atendimento_zap_apps",
            "uq_atendimento_zap_midias_numero_ref_media_id",
            "ck_atendimento_zap_midias_direcao",
            "ck_atendimento_zap_midias_status",
            "fk_atendimento_zap_midias_numero_ref_atendimento_zap_numeros",
            "fk_atendimento_zap_midias_mensagem_id_atendimento_mensagens",
        } <= nomes
        assert "ck_atendimento_canais_integracao_ou_robo" not in nomes
        assert da_migration["colunas"] == do_model["colunas"]
        assert da_migration["constraints"] == do_model["constraints"]
        assert da_migration["indices"] == do_model["indices"]
        # Os dois parciais, com o predicado certo.
        defs = {nome: d for _, nome, d in da_migration["indices"]}
        assert "WHERE ((status)::text = 'enviando'::text)" in defs["uq_atendimento_envio_em_voo"]
        assert (
            "WHERE ((status)::text = 'pendente'::text)"
            in defs["uq_atendimento_rascunho_pendente"]
        )
        # 0347: a conversa do robô é única por (canal, id externo), só sem
        # integração e com canal (a Amazon sem conta fica de fora).
        assert "(canal_id, externo_id)" in defs["uq_atendimento_conversas_robo"]
        assert (
            "WHERE ((integration_id IS NULL) AND (canal_id IS NOT NULL))"
            in defs["uq_atendimento_conversas_robo"]
        )
        # Os índices compostos do cartão "Cliente", na ordem das colunas.
        assert "(integration_id, comprador_id)" in defs[
            "ix_atendimento_pedidos_comprador_integration_id_comprador_id"
        ]
        assert "(integration_id, pedido)" in defs[
            "ix_atendimento_avaliacoes_loja_integration_id_pedido"
        ]
        # 0353: a etiqueta filtra e conta; a linha do tempo lê por conversa
        # em ordem; a reclamação vencendo, por (status, prazo).
        assert "(etiqueta)" in defs["ix_atendimento_conversas_etiqueta"]
        assert "(conversa_id, em)" in defs["ix_atendimento_etiquetas_historico_conversa_id_em"]
        assert "(status, prazo_em)" in defs["ix_atendimento_reclamacoes_status_prazo_em"]
        assert "(conversa_id)" in defs["ix_atendimento_reclamacoes_conversa_id"]
        assert "(pedido_marketplace)" in defs["ix_atendimento_reclamacoes_pedido_marketplace"]
        # 0358: a etiqueta lê só as pendentes (parcial); a aba ★ casa por
        # plataforma + pedido; o cartão "Cliente" do ML, pelo comprador.
        assert "(plataforma, pedido)" in defs["ix_atendimento_avaliacoes_loja_pendentes"]
        assert (
            "WHERE (pendente_desde IS NOT NULL)"
            in defs["ix_atendimento_avaliacoes_loja_pendentes"]
        )
        assert "(plataforma, pedido)" in defs["ix_atendimento_avaliacoes_loja_plataforma_pedido"]
        assert "(integration_id, comprador_id)" in defs[
            "ix_atendimento_avaliacoes_loja_integration_id_comprador_id"
        ]
        assert "(conversa_id)" in defs["ix_atendimento_avaliacoes_loja_conversa_id"]
        # 0362: um carrinho ABERTO por lojista por site (parcial); o
        # histórico do lojista; os comentários de uma publicação em ordem.
        assert "(site, lojista_id)" in defs["uq_atendimento_carrinhos_aberto"]
        assert "UNIQUE" in defs["uq_atendimento_carrinhos_aberto"]
        assert (
            "WHERE ((situacao)::text = 'aberto'::text)" in defs["uq_atendimento_carrinhos_aberto"]
        )
        assert "(site, lojista_id)" in defs["ix_atendimento_carrinhos_site_lojista_id"]
        assert "(publicacao_id, criado_em)" in defs[
            "ix_atendimento_comentarios_publicacao_id_criado_em"
        ]
        restricoes = {c[1]: c[3] for c in da_migration["constraints"]}
        assert (
            "(integration_id IS NOT NULL) OR (robo_perfil_id IS NOT NULL) OR "
            "(externo_ref IS NOT NULL)" in restricoes["ck_atendimento_canais_tem_origem"]
        )
        assert "ON DELETE SET NULL" in restricoes[
            "fk_atendimento_canais_rede_social_id_redes_sociais"
        ]
        assert "ON DELETE CASCADE" in restricoes["fk_atendimento_comentarios_publicacao"]
        # As colunas novas da regra (P7), com o default que deixa o manual
        # antigo como estava: tipo `categoria` sem categoria = geral.
        cols = {(c[0], c[1]): c for c in da_migration["colunas"]}
        assert cols[("atendimento_canais", "integration_id")][4] == "YES"
        assert cols[("atendimento_canais", "robo_perfil_id")][4] == "YES"
        assert cols[("atendimento_regras", "tipo")][5] == "'categoria'::character varying"
        assert cols[("atendimento_regras", "prioridade")][5] == "100"
        assert cols[("atendimento_regras", "categoria")][4] == "YES"
        assert cols[("atendimento_modelos", "categoria")][4] == "YES"
        # 0353: as conversas que já existem entram sem etiqueta (o motor
        # calcula), sem troca à mão e com a lista de secundárias vazia.
        assert cols[("atendimento_conversas", "etiqueta")][4] == "YES"
        assert cols[("atendimento_conversas", "etiqueta_manual")][4:] == ("NO", "false")
        assert cols[("atendimento_conversas", "etiquetas_secundarias")][4:] == (
            "NO",
            "'[]'::jsonb",
        )
        assert cols[("atendimento_reclamacoes", "encerrada_em")][4] == "YES"
        # 0358: nasce sem pendência, sem mídia e sem resposta pela API.
        assert cols[("atendimento_avaliacoes_loja", "pendente_desde")][4] == "YES"
        assert cols[("atendimento_avaliacoes_loja", "pode_responder")][4:] == ("NO", "false")
        assert cols[("atendimento_avaliacoes_loja", "midia")][4:] == ("NO", "'[]'::jsonb")
        # 0362: o carrinho nasce aberto, sem itens; o comentário, sem ser
        # da marca nem oculto.
        assert cols[("atendimento_canais", "externo_ref")][4] == "YES"
        assert cols[("atendimento_carrinhos", "situacao")][4:] == (
            "NO",
            "'aberto'::character varying",
        )
        assert cols[("atendimento_carrinhos", "itens")][4:] == ("NO", "'[]'::jsonb")
        assert cols[("atendimento_comentarios", "da_marca")][4:] == ("NO", "false")

        # O índice do elo conversa → pedido, igual ao do model.
        indice_bling = text(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = :s"
            " AND indexname = 'ix_bling_orders_numeroloja'"
        )
        assert "(numeroloja)" in (
            await db.execute(indice_bling, {"s": rascunho})
        ).scalar_one()
        assert "(numeroloja)" in (
            await db.execute(indice_bling, {"s": schema_model})
        ).scalar_one()

        # 0366: a fila do decidir e a do comparador (parciais) e "uma mensagem
        # do Duoke casa com uma linha só".
        assert "WHERE ((estado)::text = 'agendado'::text)" in defs[
            "ix_atendimento_automacao_registros_agendados"
        ]
        assert "WHERE ((duoke)::text = 'pendente'::text)" in defs[
            "ix_atendimento_automacao_registros_comparar"
        ]
        assert "UNIQUE" in defs["uq_atendimento_automacao_registros_duoke"]
        assert "(integration_id, automacao, devido_em)" in defs[
            "ix_atendimento_automacao_registros_tela"
        ]

        # 0379: UMA troca aberta por pedido (parcial: a concluída e a abortada não contam).
        assert "UNIQUE" in defs["uq_atendimento_trocas_aberta"]
        assert "(pedido_bling)" in defs["uq_atendimento_trocas_aberta"]
        assert "concluida" in defs["uq_atendimento_trocas_aberta"]
        assert "abortada" in defs["uq_atendimento_trocas_aberta"]
        assert cols[("atendimento_trocas", "automatica")][4:] == ("NO", "false")
        assert cols[("atendimento_trocas", "passos")][4:] == ("NO", "'[]'::jsonb")
        assert cols[("atendimento_trocas", "criado_por")][4] == "YES"
        assert cols[("atendimento_trocas", "aceite_fonte")][4] == "YES"

        # 0396: anulável, sem default e sem índice.
        assert cols[("atendimento_conversas", "ultima_loja_qualquer_em")][2] == "timestamptz"
        assert cols[("atendimento_conversas", "ultima_loja_qualquer_em")][4:] == ("YES", None)

        # 0399: anulável, sem default e sem índice.
        assert cols[("atendimento_conversas", "visto_em")][2] == "timestamptz"
        assert cols[("atendimento_conversas", "visto_em")][4:] == ("YES", None)

        # 0416: o JSON nasce vazio; as datas, anuláveis.
        assert cols[("atendimento_canais", "notificacoes_migracao")][4:] == ("NO", "'{}'::jsonb")
        assert cols[("atendimento_conversas", "notificacoes_corte_em")][4:] == ("YES", None)
        assert cols[("atendimento_conversas", "notificacoes_iniciais")][4:] == ("NO", "'{}'::jsonb")
        assert cols[("atendimento_conversas", "notificacoes_visto_em")][4:] == ("YES", None)
        # 0420: o canal nasce SEM o agente e sem assunto de orientação liberado.
        assert cols[("atendimento_canais", "agente")][4:] == ("NO", "false")
        assert cols[("atendimento_canais", "auto_orientacao")][4:] == ("NO", "'[]'::jsonb")
        assert cols[("atendimento_canais", "auto_agente")][4:] == ("NO", "'[]'::jsonb")

        # 0430: TUDO nasce desligado — o número nasce sem envio, em preparo, sem
        # lista de teste e sem tentativa de registro; o app nasce ativo e de
        # produção; o contato, sem bloqueio; o evento novo, sem `proxima_em`.
        assert cols[("atendimento_zap_numeros", "envio_ligado")][4:] == ("NO", "false")
        assert cols[("atendimento_zap_numeros", "etapa")][4:] == (
            "NO",
            "'preparando'::character varying",
        )
        assert cols[("atendimento_zap_numeros", "destinos_teste")][2] == "_varchar"
        assert cols[("atendimento_zap_numeros", "destinos_teste")][4:] == (
            "NO",
            "'{}'::character varying[]",
        )
        assert cols[("atendimento_zap_numeros", "registro_tentativas")][2] == "_timestamptz"
        assert cols[("atendimento_zap_numeros", "pin_enc")][2:5] == ("bytea", None, "YES")
        assert cols[("atendimento_zap_apps", "segredo_enc")][2:5] == ("bytea", None, "NO")
        assert cols[("atendimento_zap_apps", "verify_token_enc")][2:5] == ("bytea", None, "NO")
        assert cols[("atendimento_zap_apps", "teste")][4:] == ("NO", "false")
        assert cols[("atendimento_zap_apps", "ativo")][4:] == ("NO", "true")
        assert cols[("atendimento_zap_contas", "token_enc")][2:5] == ("bytea", None, "NO")
        assert cols[("atendimento_zap_contas", "token_nunca_vence")][4:] == ("NO", "false")
        assert cols[("atendimento_zap_contatos", "bloqueado")][4:] == ("NO", "false")
        assert cols[("atendimento_zap_eventos", "id")][2] == "int8"
        assert cols[("atendimento_zap_eventos", "proxima_em")][4:] == ("YES", None)
        assert cols[("atendimento_zap_eventos", "recebido_em")][4:] == ("NO", "now()")
        assert cols[("atendimento_zap_midias", "status")][4:] == (
            "NO",
            "'pendente'::character varying",
        )
        assert cols[("atendimento_zap_midias", "sha256_local")][2:4] == ("bpchar", 64)
        # Os dois UNIQUE PARCIAIS do contato (P3) e a fila (o evento novo, com
        # `proxima_em` NULL, primeiro; só o que falta processar).
        assert "UNIQUE" in defs["uq_atendimento_zap_contatos_bsuid"]
        assert "(numero_ref, bsuid)" in defs["uq_atendimento_zap_contatos_bsuid"]
        assert "WHERE (bsuid IS NOT NULL)" in defs["uq_atendimento_zap_contatos_bsuid"]
        assert "(numero_ref, wa_id_norm)" in defs["uq_atendimento_zap_contatos_wa_id_norm"]
        assert "WHERE (wa_id_norm IS NOT NULL)" in defs["uq_atendimento_zap_contatos_wa_id_norm"]
        assert "(proxima_em NULLS FIRST, id)" in defs["ix_atendimento_zap_eventos_fila"]
        assert "WHERE (processado_em IS NULL)" in defs["ix_atendimento_zap_eventos_fila"]
        assert "UNIQUE" not in defs["ix_atendimento_zap_eventos_fila"]
        assert "(mensagem_id)" in defs["ix_atendimento_zap_midias_mensagem_id"]
        assert "ON DELETE RESTRICT" in restricoes[
            "fk_atendimento_zap_contas_app_ref_atendimento_zap_apps"
        ]
        assert "ON DELETE RESTRICT" in restricoes[
            "fk_atendimento_zap_numeros_conta_ref_atendimento_zap_contas"
        ]
        assert "ON DELETE RESTRICT" in restricoes["fk_atendimento_zap_contatos_numero"]
        assert "ON DELETE SET NULL" in restricoes["fk_atendimento_zap_numeros_marca_id_marcas"]
        assert "ON DELETE SET NULL" in restricoes[
            "fk_atendimento_zap_numeros_canal_id_atendimento_canais"
        ]
        assert "ON DELETE SET NULL" in restricoes[
            "fk_atendimento_zap_contatos_conversa_id_atendimento_conversas"
        ]
        assert "ON DELETE CASCADE" in restricoes[
            "fk_atendimento_zap_midias_mensagem_id_atendimento_mensagens"
        ]
        assert "'aguardando_duoke'" in restricoes["ck_atendimento_zap_numeros_etapa"]
        assert "IS NULL" in restricoes["ck_atendimento_zap_contatos_opt_out_tipo"]
        assert "IS NULL" in restricoes["ck_atendimento_zap_eventos_resultado"]

        # O downgrade da 0430 volta EXATAMENTE ao catálogo de depois da 0420.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", (zap,))
        await db.commit()
        assert await _catalogo(db, rascunho, rascunho) == antes_da_0430
        assert not [t for t in antes_da_0430["tabelas"] if t.startswith("atendimento_zap_")]

        # O downgrade da 0420 volta EXATAMENTE ao catálogo de depois da 0416
        # (e as tabelas dela somem).
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", (agente,))
        await db.commit()
        assert await _catalogo(db, rascunho, rascunho) == antes_da_0420
        restantes = {
            r[0]
            for r in (
                await db.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema = :s"
                    ),
                    {"s": rascunho},
                )
            ).all()
        }
        assert not restantes & (DE_OUTRA_MIGRATION - {"atendimento_regras_pasta_email"})

        # O downgrade da 0416 volta EXATAMENTE ao catálogo de depois da 0399.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", (notificacoes,))
        await db.commit()
        assert await _catalogo(db, rascunho, rascunho) == antes_da_0416

        # O downgrade da 0399 volta EXATAMENTE ao catálogo de depois da 0396.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", (visto,))
        await db.commit()
        assert await _catalogo(db, rascunho, rascunho) == antes_da_0399

        # O downgrade da 0396 volta EXATAMENTE ao catálogo de depois da 0383.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", (falta,))
        await db.commit()
        assert await _catalogo(db, rascunho, rascunho) == antes_da_0396

        # O downgrade da 0379 volta EXATAMENTE ao catálogo de depois da 0371.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", (trocas,))
        await db.commit()
        assert await _catalogo(db, rascunho, rascunho) == antes_da_0379

        # O downgrade da 0366 volta EXATAMENTE ao catálogo de depois da 0362.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", (automacoes,))
        await db.commit()
        assert await _catalogo(db, rascunho, rascunho) == antes_da_0366

        # O downgrade da 0362 volta EXATAMENTE ao catálogo de depois da 0358 —
        # o canal externo some, a conversa dele fica (sem canal).
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", (externo,))
        await db.commit()
        assert await _catalogo(db, rascunho, rascunho) == antes_da_0362
        sobra = (
            await db.execute(
                text(
                    f'SELECT (SELECT count(*) FROM "{rascunho}".atendimento_canais), '
                    f'(SELECT count(canal_id) FROM "{rascunho}".atendimento_conversas), '
                    f'(SELECT count(*) FROM "{rascunho}".atendimento_conversas)'
                )
            )
        ).one()
        assert tuple(sobra) == (1, 0, 1)
        await db.execute(text(f'DELETE FROM "{rascunho}".atendimento_conversas'))
        await db.execute(text(f'DELETE FROM "{rascunho}".atendimento_canais'))
        await db.commit()

        # O downgrade da 0358 volta EXATAMENTE ao catálogo de depois da 0353.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", (aval,))
        await db.commit()
        assert await _catalogo(db, rascunho, rascunho) == antes_da_0358

        # O downgrade da 0353 volta EXATAMENTE ao catálogo de depois da 0347.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", (etiq,))
        await db.commit()
        assert await _catalogo(db, rascunho, rascunho) == antes_da_0353
        assert (await db.execute(indice_bling, {"s": rascunho})).first() is None

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", (robo, mod))
        await db.commit()
        depois = await _catalogo(db, rascunho, rascunho)
        assert depois["tabelas"] == []
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
