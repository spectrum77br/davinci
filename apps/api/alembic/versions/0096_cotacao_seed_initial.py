# ruff: noqa: E501, S608
"""Seed inicial da aba Cotação — 2 fabricantes + 15 produtos + valores.

Replica a planilha-mãe (IMPORTAÇÃO.xlsx, aba cotação). Fabricantes:
  * dtlugagge — 4 observações
  * omaska    — sem observações

Idempotente: skip o seed inteiro se já existir fabricante "dtlugagge".
Roda só na primeira aplicação; se o operador editar/excluir depois,
um re-run não rastreia esses produtos/valores como "do seed".

Notas:
  * `capacidade` é texto livre — 1336/900 são valores que o operador
    escreveu literalmente na planilha (parecem ser cm³ de capacidade
    interna, mas a coluna armazena exatamente o que ele digitou).
  * `valor_real`/`valor_usd` em NUMERIC(12,2); ponto como separador
    decimal (a planilha original usava vírgula).
  * Linhas onde os 3 campos seriam nulos NÃO geram registro em
    cotacao_valores — o frontend já lida com células ausentes.

Revision ID: 0096_cotacao_seed_initial
Revises: 0095_merge_cotacao_endereco
Create Date: 2026-05-26
"""

from collections.abc import Sequence
from uuid import uuid4

from sqlalchemy import text

from alembic import op

revision: str = "0096_cotacao_seed_initial"
down_revision: str | None = "0095_merge_cotacao_endereco"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    conn = op.get_bind()

    # Idempotência: skip se o fabricante "dtlugagge" já existe (case-insensitive).
    existing = conn.execute(text(
        f"SELECT id FROM {SCHEMA}.cotacao_fabricantes WHERE LOWER(nome) = 'dtlugagge' LIMIT 1"
    )).first()
    if existing is not None:
        return

    fab_dt = uuid4()
    fab_om = uuid4()

    conn.execute(
        text(f"""
            INSERT INTO {SCHEMA}.cotacao_fabricantes
                (id, nome, obs1, obs2, obs3, obs4, ordem)
            VALUES
                (:id, :nome, :o1, :o2, :o3, :o4, :ord)
        """),
        [
            {
                "id": str(fab_dt), "nome": "dtlugagge", "ord": 1,
                "o1": "modulo ABS/PC de 8/14/15/18/20/24 usd 2400",
                "o2": "abs + pc aumentar usd3, pc usd7 por set",
                "o3": "tela divisorio com ziper usd 0,20 20, usd 0,30 24",
                "o4": "ziper duplo usd 1,2",
            },
            {
                "id": str(fab_om), "nome": "omaska", "ord": 2,
                "o1": None, "o2": None, "o3": None, "o4": None,
            },
        ],
    )

    produto_nomes = [
        "kit 8 silicone",
        "toy",
        "encosto cabeça",
        "mochila",
        "necessaire 8 ABS",
        "necessaire 12 ABS",
        "necessaire 13 ABS",
        "mala 18 ABS",
        "mala 20 ABS",
        "mala 24 ABS",
        "necessaire 14 PP",
        "mala 20 PP",
        "mala 24 PP",
        "mala 20 ABS executivo",
        "mala 20 PC executivo",
    ]
    prod_ids: dict[str, str] = {nome: str(uuid4()) for nome in produto_nomes}

    conn.execute(
        text(f"""
            INSERT INTO {SCHEMA}.cotacao_produtos (id, nome, ordem)
            VALUES (:id, :nome, :ord)
        """),
        [
            {"id": prod_ids[nome], "nome": nome, "ord": idx + 1}
            for idx, nome in enumerate(produto_nomes)
        ],
    )

    # (fabricante_id, produto_nome, capacidade, valor_real, valor_usd)
    # Apenas linhas com pelo menos 1 valor preenchido.
    valores: list[tuple[str, str, str | None, float | None, float | None]] = [
        # dtlugagge
        (str(fab_dt), "necessaire 8 ABS",     None,   18.90,   1.50),
        (str(fab_dt), "necessaire 12 ABS",    None,   41.00,   3.00),
        (str(fab_dt), "necessaire 13 ABS",    None,   41.00,   3.50),
        (str(fab_dt), "mala 18 ABS",          None,  100.00,   7.70),
        (str(fab_dt), "mala 20 ABS",          "1336", 115.00,  8.70),
        (str(fab_dt), "mala 24 ABS",          "900",  126.00, 10.10),
        (str(fab_dt), "necessaire 14 PP",     None,   46.00,   3.70),
        (str(fab_dt), "mala 20 PP",           "1336", 127.00,  9.20),
        (str(fab_dt), "mala 24 PP",           "900",  139.00, 10.60),
        (str(fab_dt), "mala 20 ABS executivo", "1336", 230.00, 17.30),
        # omaska
        (str(fab_om), "kit 8 silicone",       None,    5.00,   0.30),
        (str(fab_om), "toy",                  None,    9.00,   0.80),
        (str(fab_om), "encosto cabeça",       None,   18.00,   1.50),
        (str(fab_om), "mochila",              None,   80.00,   6.70),
        (str(fab_om), "necessaire 14 PP",     None,   49.00,   4.00),
        (str(fab_om), "mala 20 PP",           "1336", 135.00, 10.00),
        (str(fab_om), "mala 24 PP",           "900",  148.00, 11.00),
        (str(fab_om), "mala 20 PC executivo", "1336", 332.00, 25.00),
    ]

    conn.execute(
        text(f"""
            INSERT INTO {SCHEMA}.cotacao_valores
                (id, fabricante_id, produto_id, capacidade, valor_real, valor_usd)
            VALUES
                (:id, :fab, :prod, :cap, :vr, :vu)
        """),
        [
            {
                "id": str(uuid4()),
                "fab": fab_id,
                "prod": prod_ids[prod_nome],
                "cap": cap,
                "vr": vr,
                "vu": vu,
            }
            for fab_id, prod_nome, cap, vr, vu in valores
        ],
    )


def downgrade() -> None:
    # Apaga só as linhas dessas seed (identificadas pelos nomes de fabricante).
    # ON DELETE CASCADE em cotacao_valores limpa os valores.
    conn = op.get_bind()
    conn.execute(text(f"""
        DELETE FROM {SCHEMA}.cotacao_fabricantes
        WHERE LOWER(nome) IN ('dtlugagge', 'omaska')
    """))
    # Produtos do seed (matching by exact name list).
    conn.execute(text(f"""
        DELETE FROM {SCHEMA}.cotacao_produtos
        WHERE nome IN (
            'kit 8 silicone','toy','encosto cabeça','mochila',
            'necessaire 8 ABS','necessaire 12 ABS','necessaire 13 ABS',
            'mala 18 ABS','mala 20 ABS','mala 24 ABS',
            'necessaire 14 PP','mala 20 PP','mala 24 PP',
            'mala 20 ABS executivo','mala 20 PC executivo'
        )
    """))
