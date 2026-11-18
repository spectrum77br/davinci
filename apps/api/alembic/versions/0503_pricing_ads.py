"""Aba Contas: Ads com Geral/Por produto, % que pode gastar e gasto por dia

Pedido M-12 do dono (prévia aprovada em 18/11/2026): a coluna Ads da aba Contas
era texto livre (`pricing_accounts.ads`: "2% - 50 ROAS", "0"). Vira um painel
igual ao da Margem (0499), guardado em estrutura própria:

* `pricing_ads_conta`: a Geral de uma conta (`pricing_accounts.id`): as caixas
  Geral e Por produto, o % que pode gastar (0 a 100; 2 = 2%) e o gasto por dia
  em R$. Sem linha = as duas caixas ligadas, sem % e sem gasto.
* `pricing_ads_produto`: o % e o gasto por dia de um produto (linha da Tabela,
  `pricing_products.id`) na conta. Sem linha = usa a Geral.

O ROAS mínimo (100 ÷ %) é calculado na hora, não é gravado. Nada disso entra no
cálculo de preço. Apagar a conta ou a linha da Tabela leva o que é dela
(CASCADE).

CONVERSÃO (upgrade): "2% - 50 ROAS" → Geral 2 (o ROAS digitado sai: agora é
calculado); "2%" ou "2" → 2; "0" → 0. O que não converte ("3-4% - 33-25 -
R$3000 25.08", "Sudeste R$ 50 Diario") vai para a Obs (`observation`) da conta,
depois do que já existe, separado por " | " e com o nome do campo: "Ads: …".
O texto antigo (`ads`) fica no banco, fora da tela.

DOWNGRADE: tira da Obs exatamente o que o upgrade somou (o `ads` não mudou,
então a conversão se refaz igual; Obs editada depois fica como está) e apaga
as duas tabelas (o que foi gravado nelas se perde).

Revision ID: 0503_pricing_ads
Revises: 0502_contas_campos_proprios
"""

# ruff: noqa: S608 — SQL montado só com nomes fixos desta migration (schema), sem entrada de fora
import re
from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0503_pricing_ads"
down_revision: str | None = "0502_contas_campos_proprios"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

# FIXOS (não importam a constante viva do modelo).
CHECK_PCT = "pode_gastar_pct BETWEEN 0 AND 100"
CHECK_GASTO = "gasto_dia >= 0"
# "2% - 50 ROAS", "2%", "2", "4,5%": o % no começo e, se vier, o ROAS digitado.
_RE_ADS = re.compile(
    r"\s*(\d{1,3}(?:[.,]\d{1,2})?)\s*%?(?:\s*-\s*\d+(?:[.,]\d+)?\s*roas)?\s*", re.I
)
SEPARADOR_OBS = " | "


def converter_ads(texto: str | None) -> tuple[Decimal | None, str | None]:
    """(% da Geral de 0 a 100 ou None, o que vai para a Obs ou None)."""
    if texto is None or not texto.strip():
        return None, None
    m = _RE_ADS.fullmatch(texto)
    if m:
        valor = Decimal(m[1].replace(",", "."))
        if valor <= 100:
            return valor, None
    return None, f"Ads: {texto.strip()}"


def somar_obs(obs: str | None, nota: str | None) -> str | None:
    """A nota entra depois do que já existe na Obs."""
    if not nota:
        return obs
    return f"{obs}{SEPARADOR_OBS}{nota}" if obs else nota


def tirar_da_obs(obs: str | None, nota: str | None) -> str | None:
    """O contrário de `somar_obs` (downgrade). Obs mexida depois fica como está."""
    if not nota or obs is None:
        return obs
    if obs == nota:
        return None
    if obs.endswith(SEPARADOR_OBS + nota):
        return obs[: -len(SEPARADOR_OBS + nota)]
    return obs


def _contas_com_ads(bind):
    return (
        bind.execute(
            sa.text(
                f"""
                SELECT id, ads, observation
                  FROM {SCHEMA}.pricing_accounts
                 WHERE nullif(btrim(ads), '') IS NOT NULL
                """
            )
        )
        .mappings()
        .all()
    )


def converter(bind) -> None:
    """Grava a Geral a partir do texto e soma à Obs o que não converte."""
    for conta in _contas_com_ads(bind):
        pct, nota = converter_ads(conta["ads"])
        if pct is not None:
            bind.execute(
                sa.text(
                    f"INSERT INTO {SCHEMA}.pricing_ads_conta (pricing_account_id, pode_gastar_pct)"
                    " VALUES (:id, :pct)"
                ),
                {"id": conta["id"], "pct": pct},
            )
        if nota:
            bind.execute(
                sa.text(f"UPDATE {SCHEMA}.pricing_accounts SET observation = :obs WHERE id = :id"),
                {"obs": somar_obs(conta["observation"], nota), "id": conta["id"]},
            )


def desfazer_obs(bind) -> None:
    for conta in _contas_com_ads(bind):
        _, nota = converter_ads(conta["ads"])
        obs = tirar_da_obs(conta["observation"], nota)
        if obs != conta["observation"]:
            bind.execute(
                sa.text(f"UPDATE {SCHEMA}.pricing_accounts SET observation = :obs WHERE id = :id"),
                {"obs": obs, "id": conta["id"]},
            )


def _quem_e_quando() -> list[sa.Column]:
    return [
        sa.Column(
            "atualizado_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def _checks(tabela: str) -> list[sa.CheckConstraint]:
    # Nome final (`op.f`), o mesmo que a convenção ck_<tabela>_<nome> dá ao modelo.
    return [
        sa.CheckConstraint(CHECK_PCT, name=op.f(f"ck_{tabela}_pode_gastar_pct")),
        sa.CheckConstraint(CHECK_GASTO, name=op.f(f"ck_{tabela}_gasto_dia")),
    ]


def _conta() -> sa.Column:
    return sa.Column(
        "pricing_account_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(f"{SCHEMA}.pricing_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )


def upgrade() -> None:
    op.create_table(
        "pricing_ads_conta",
        _conta(),
        sa.Column("ads_geral_ligada", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("pode_gastar_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("gasto_dia", sa.Numeric(12, 2), nullable=True),
        sa.Column(
            "ads_por_produto_ligada", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        *_quem_e_quando(),
        *_checks("pricing_ads_conta"),
        schema=SCHEMA,
    )
    op.create_table(
        "pricing_ads_produto",
        _conta(),
        sa.Column(
            "pricing_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.pricing_products.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("pode_gastar_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("gasto_dia", sa.Numeric(12, 2), nullable=True),
        *_quem_e_quando(),
        *_checks("pricing_ads_produto"),
        schema=SCHEMA,
    )
    converter(op.get_bind())


def downgrade() -> None:
    desfazer_obs(op.get_bind())
    op.drop_table("pricing_ads_produto", schema=SCHEMA)
    op.drop_table("pricing_ads_conta", schema=SCHEMA)
