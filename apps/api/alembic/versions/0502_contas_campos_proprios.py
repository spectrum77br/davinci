"""Aba Contas: campos próprios para Desconto até, Cupom até, Afiliado % e Oferta limite %

Pedido M-11 do dono (prévia aprovada em 18/11/2026): Desconto, Afiliado, Cupom e
Oferta eram texto livre (`discount`, `affiliate`, `coupon`, `offer`), sem
validação ("26.06", "4%", "7% (amostra 3 vendas)"). Viram colunas próprias:

- `desconto_ate` / `cupom_ate`: texto "DD/MM" (até que dia e mês vão o desconto
  e o cupom da conta). Sem ano; o CHECK só aceita dia e mês que existem
  (29/02 vale). Texto e não `date` porque não tem ano; texto e não dois
  números porque a tela mostra e grava "26/06" inteiro.
- `afiliado_pct` / `oferta_limite_pct`: NUMERIC(5,2), de 0 a 100 (4 = 4%).

Nada disso entra no cálculo de preço. As colunas antigas ficam como estão no
banco (só saem da tela).

CONVERSÃO (upgrade), sem perder nada:
- datas: "26.06" → 26/06, "31.12" → 31/12, "05.04" → 05/04;
- %: "4%" → 4, "4" → 4, "7% (amostra 3 vendas)" → 7;
- o que sobra depois do valor ("amostra 3 vendas", "1%RE" de "31.12 - 1%RE") e
  o que não converte ("0" no desconto, "valor minimo alto d+" no cupom) vão para
  a Obs (`observation`) da conta, depois do que já existe lá, separados por
  " | " e com o nome do campo antigo: "Afiliado: amostra 3 vendas".

DOWNGRADE: tira da Obs exatamente o que o upgrade somou (as colunas antigas não
mudaram, então a conversão se refaz igual; Obs editada depois fica como está) e
apaga as quatro colunas.

Revision ID: 0502_contas_campos_proprios
Revises: 0501_conciliacao_temu_lojas
"""

# ruff: noqa: S608 — SQL montado só com nomes fixos desta migration (schema), sem entrada de fora
import re
from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision: str = "0502_contas_campos_proprios"
down_revision: str | None = "0501_conciliacao_temu_lojas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

# FIXO (não importa a constante viva do modelo): dia/mês sem ano.
DIA_MES_REGEX = "^((0[1-9]|1[0-9]|2[0-9])/(0[1-9]|1[0-2])|30/(0[13-9]|1[0-2])|31/(0[13578]|1[02]))$"
_DIAS_NO_MES = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
# "26.06", "26/6", "31.12 - 1%RE": dia e mês no começo; o resto é sobra.
_RE_DIA_MES = re.compile(r"\s*(\d{1,2})\s*[./-]\s*(\d{1,2})(?!\d)(.*)", re.S)
# "4%", "4", "7% (amostra 3 vendas)", "4,5%": o número no começo; o resto é sobra.
_RE_PCT = re.compile(r"\s*(\d{1,3}(?:[.,]\d{1,2})?)(?![\d.,])\s*%?(.*)", re.S)

# (coluna antiga, coluna nova, tipo, nome na Obs)
CAMPOS = (
    ("discount", "desconto_ate", "data", "Desconto"),
    ("affiliate", "afiliado_pct", "pct", "Afiliado"),
    ("coupon", "cupom_ate", "data", "Cupom"),
    ("offer", "oferta_limite_pct", "pct", "Oferta"),
)
SEPARADOR_OBS = " | "


def _sobra(resto: str) -> str | None:
    """Tira separador e parênteses: ' - 1%RE' → '1%RE', ' (amostra 3 vendas)' →
    'amostra 3 vendas'. Vazio → None."""
    resto = resto.strip().lstrip("-–.,;:/").strip()
    if resto.startswith("(") and resto.endswith(")"):
        resto = resto[1:-1].strip()
    return resto or None


def converter_data(texto: str | None) -> tuple[str | None, str | None]:
    """(valor "DD/MM" ou None, o que vai para a Obs ou None)."""
    if texto is None or not texto.strip():
        return None, None
    m = _RE_DIA_MES.fullmatch(texto)
    if m:
        dia, mes = int(m[1]), int(m[2])
        if 1 <= mes <= 12 and 1 <= dia <= _DIAS_NO_MES[mes - 1]:
            return f"{dia:02d}/{mes:02d}", _sobra(m[3])
    return None, texto.strip()


def converter_pct(texto: str | None) -> tuple[Decimal | None, str | None]:
    """(valor de 0 a 100 ou None, o que vai para a Obs ou None)."""
    if texto is None or not texto.strip():
        return None, None
    m = _RE_PCT.fullmatch(texto)
    if m:
        valor = Decimal(m[1].replace(",", "."))
        if valor <= 100:
            return valor, _sobra(m[2])
    return None, texto.strip()


def converter_conta(antigos) -> tuple[dict, list[str]]:
    """Valores das colunas antigas → ({coluna nova: valor}, notas para a Obs)."""
    novos: dict = {}
    notas: list[str] = []
    for antiga, nova, tipo, nome in CAMPOS:
        conversor = converter_data if tipo == "data" else converter_pct
        valor, sobra = conversor(antigos[antiga])
        novos[nova] = valor
        if sobra:
            notas.append(f"{nome}: {sobra}")
    return novos, notas


def somar_obs(obs: str | None, notas: list[str]) -> str | None:
    """As notas entram depois do que já existe na Obs."""
    if not notas:
        return obs
    juntas = SEPARADOR_OBS.join(notas)
    return f"{obs}{SEPARADOR_OBS}{juntas}" if obs else juntas


def tirar_da_obs(obs: str | None, notas: list[str]) -> str | None:
    """O contrário de `somar_obs` (downgrade). Obs mexida depois fica como está."""
    if not notas or obs is None:
        return obs
    juntas = SEPARADOR_OBS.join(notas)
    if obs == juntas:
        return None
    if obs.endswith(SEPARADOR_OBS + juntas):
        return obs[: -len(SEPARADOR_OBS + juntas)]
    return obs


def _contas_com_texto(bind):
    return (
        bind.execute(
            sa.text(
                f"""
                SELECT id, discount, affiliate, coupon, offer, observation
                  FROM {SCHEMA}.pricing_accounts
                 WHERE coalesce(discount, affiliate, coupon, offer) IS NOT NULL
                """
            )
        )
        .mappings()
        .all()
    )


def converter(bind) -> None:
    """Preenche as colunas novas a partir das antigas e soma as sobras à Obs."""
    for conta in _contas_com_texto(bind):
        novos, notas = converter_conta(conta)
        bind.execute(
            sa.text(
                f"""
                UPDATE {SCHEMA}.pricing_accounts
                   SET desconto_ate = :desconto_ate, afiliado_pct = :afiliado_pct,
                       cupom_ate = :cupom_ate, oferta_limite_pct = :oferta_limite_pct,
                       observation = :observation
                 WHERE id = :id
                """
            ),
            {**novos, "observation": somar_obs(conta["observation"], notas), "id": conta["id"]},
        )


def desfazer_obs(bind) -> None:
    for conta in _contas_com_texto(bind):
        _, notas = converter_conta(conta)
        obs = tirar_da_obs(conta["observation"], notas)
        if obs != conta["observation"]:
            bind.execute(
                sa.text(f"UPDATE {SCHEMA}.pricing_accounts SET observation = :obs WHERE id = :id"),
                {"obs": obs, "id": conta["id"]},
            )


def upgrade() -> None:
    for coluna in ("desconto_ate", "cupom_ate"):
        op.add_column(
            "pricing_accounts", sa.Column(coluna, sa.Text(), nullable=True), schema=SCHEMA
        )
        op.create_check_constraint(
            op.f(f"ck_pricing_accounts_{coluna}"),
            "pricing_accounts",
            f"{coluna} ~ '{DIA_MES_REGEX}'",
            schema=SCHEMA,
        )
    for coluna in ("afiliado_pct", "oferta_limite_pct"):
        op.add_column(
            "pricing_accounts", sa.Column(coluna, sa.Numeric(5, 2), nullable=True), schema=SCHEMA
        )
        op.create_check_constraint(
            op.f(f"ck_pricing_accounts_{coluna}"),
            "pricing_accounts",
            f"{coluna} BETWEEN 0 AND 100",
            schema=SCHEMA,
        )
    converter(op.get_bind())


def downgrade() -> None:
    desfazer_obs(op.get_bind())
    # Apagar a coluna apaga o CHECK dela.
    for coluna in ("oferta_limite_pct", "afiliado_pct", "cupom_ate", "desconto_ate"):
        op.drop_column("pricing_accounts", coluna, schema=SCHEMA)
