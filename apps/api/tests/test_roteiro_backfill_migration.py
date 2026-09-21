"""Backfill da 0299: o rename da equipe e os 2 roteiros que já existem.

O conftest monta o schema com `Base.metadata.create_all` e NÃO roda alembic,
então nada aqui testaria a migration sozinho. Este arquivo segue o molde do
`test_importacao_config_migration.py`: reproduz o estado de produção no nível
do SQL e roda as SENTENÇAS DE VERDADE da migration — importadas dela, nunca
copiadas, senão o teste envelhece calado provando o comportamento antigo.

Estado de produção medido em 21/09/2026: 49 linhas em `marketing_creatives`,
todas com `equipe = '1'`, e 2 delas com roteiro escrito. Um usuário com
`marketing_teams = ["1"]`, um com JSON `null` gravado e 20 com NULL.

O que este arquivo trava:
- a equipe "1" vira "Bill Gates" nos dois lugares (criativos e usuários);
- o usuário com JSON `null` não derruba a migração (o
  `jsonb_array_elements_text` estoura em cima dele sem a guarda);
- "10" e "1a" NÃO são renomeados junto com "1";
- os roteiros migrados nascem DESLIGADOS — migração não escancara pra agência
  o que hoje ninguém de fora vê;
- criativo sem texto não gera roteiro fantasma.
"""
# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MarketingCreative, UserRole

SCHEMA = "davinci_test"

_caminho = (
    Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "0299_roteiro_personagem.py"
)
_spec = importlib.util.spec_from_file_location("m0299", _caminho)
m0299 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m0299)


async def _rodar_backfill(db: AsyncSession) -> None:
    for sentenca in m0299.sentencas_backfill(SCHEMA):
        await db.execute(sentenca)
    await db.commit()


async def _criativo(db: AsyncSession, *, roteiro: str | None, equipe: str | None = "1"):
    c = MarketingCreative(
        modelo="video 30s", marca="uranyx", sku="dg023", equipe=equipe, roteiro=roteiro
    )
    db.add(c)
    await db.flush()
    return c


async def test_equipe_1_vira_bill_gates_nos_criativos(db: AsyncSession):
    a = await _criativo(db, roteiro=None)
    b = await _criativo(db, roteiro=None, equipe="outra")
    await db.commit()

    await _rodar_backfill(db)
    await db.refresh(a)
    await db.refresh(b)
    assert a.equipe == "Bill Gates"
    assert b.equipe == "outra", "só a equipe '1' muda de nome"


async def test_equipe_1_vira_bill_gates_nos_usuarios(db: AsyncSession, make_user):
    u = await make_user(role=UserRole.USER)
    await db.execute(
        text(f'UPDATE "{SCHEMA}".users SET marketing_teams = CAST(:t AS jsonb) WHERE id = :i')
        .bindparams(t='["1", "outra"]', i=u.id)
    )
    await db.commit()

    await _rodar_backfill(db)
    teams = (
        await db.execute(
            text(f'SELECT marketing_teams FROM "{SCHEMA}".users WHERE id = :i')
            .bindparams(i=u.id)
        )
    ).scalar_one()
    assert teams == ["Bill Gates", "outra"], "renomeia no lugar e preserva a ordem"


async def test_usuario_com_json_null_nao_derruba_a_migracao(db: AsyncSession, make_user):
    """Existe em produção: `marketing_teams` com o JSON `null` gravado dentro.
    Sem a guarda `jsonb_typeof = 'array'`, o jsonb_array_elements_text estoura
    e a migration inteira aborta no meio."""
    u = await make_user(role=UserRole.USER)
    await db.execute(
        text(f'UPDATE "{SCHEMA}".users SET marketing_teams = \'null\'::jsonb WHERE id = :i')
        .bindparams(i=u.id)
    )
    await db.commit()

    await _rodar_backfill(db)  # não pode levantar
    teams = (
        await db.execute(
            text(f'SELECT marketing_teams FROM "{SCHEMA}".users WHERE id = :i')
            .bindparams(i=u.id)
        )
    ).scalar_one()
    assert teams is None


async def test_equipe_10_nao_e_confundida_com_1(db: AsyncSession, make_user):
    """`?` casa a chave exata. Um `LIKE '%1%'` renomearia '10' e '1a' junto."""
    u = await make_user(role=UserRole.USER)
    await db.execute(
        text(f'UPDATE "{SCHEMA}".users SET marketing_teams = CAST(:t AS jsonb) WHERE id = :i')
        .bindparams(t='["10", "1a"]', i=u.id)
    )
    c = await _criativo(db, roteiro=None, equipe="10")
    await db.commit()

    await _rodar_backfill(db)
    teams = (
        await db.execute(
            text(f'SELECT marketing_teams FROM "{SCHEMA}".users WHERE id = :i')
            .bindparams(i=u.id)
        )
    ).scalar_one()
    await db.refresh(c)
    assert teams == ["10", "1a"]
    assert c.equipe == "10"


async def test_roteiro_migrado_nasce_desligado_e_endereçado(db: AsyncSession):
    """A decisão do Eduardo: os 2 textos de produção migram escondidos.

    Hoje eles são invisíveis para as duas agências (moram na equipe '1', que
    não casa com token nenhum). `ativo = false` garante que o `alembic upgrade`
    não os entrega pra fora sem ninguém clicar em nada.
    """
    com = await _criativo(db, roteiro="  Vertical UGC 9:16, <<<48dbb6ed>>> (Lívia)  ")
    sem = await _criativo(db, roteiro=None)
    branco = await _criativo(db, roteiro="   ")
    await db.commit()

    await _rodar_backfill(db)
    linhas = (
        await db.execute(
            text(
                f"SELECT id, titulo, texto, equipe_destino, ativo "
                f'FROM "{SCHEMA}".marketing_roteiros'
            )
        )
    ).all()
    assert len(linhas) == 1, "só o criativo COM texto vira roteiro"
    (rid, titulo, texto, destino, ativo) = linhas[0]
    assert rid == com.id, "o id do roteiro é o do criativo — determinístico"
    assert titulo == "video 30s"
    assert texto == "Vertical UGC 9:16, <<<48dbb6ed>>> (Lívia)", "btrim nas pontas"
    assert destino == "Bill Gates", "herda o recorte de hoje, já renomeado"
    assert ativo is False, "migração NÃO publica pra agência"

    await db.refresh(com)
    await db.refresh(sem)
    await db.refresh(branco)
    assert com.roteiro_id == com.id
    assert sem.roteiro_id is None
    assert branco.roteiro_id is None, "espaço em branco não é roteiro"
