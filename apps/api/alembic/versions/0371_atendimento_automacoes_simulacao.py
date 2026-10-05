"""atendimento: semeia em SIMULAR as automações do Duoke que faltavam (só dado, sem tabela)

Eduardo, 05/10/2026 (à noite): "por enquanto deixe só pra mostrar que ele
enviaria mesmo corretamente, mas não enviar essas mensagens padrão; quero todas
do Duoke aqui também em todas as plataformas possíveis". O catálogo
(`services/atendimento/automacoes_catalogo.py`) ganhou as que faltavam — todas
SÓ SIMULAM (`Automacao.so_simular`: o PATCH recusa `enviar`, o motor decide em
`simular` e o `enviar.py` recusa a automação):

  • `shopee_nao_pago` — o "carrinho" do Duoke: pedido criado e não pago, 30 min
    depois, com o cupom pela faixa do valor (12 lojas; a Kia e a Aguiar não);
  • `shopee_avaliacao_boa` / `shopee_avaliacao_ruim` — a resposta da avaliação,
    pública + chat, 4–5★ e 1–3★ (13 lojas; a Aguiar responde à mão);
  • `tiktok_pedido_recebido` — o cartão do pedido + o texto, 5 min depois do
    aviso de pedido da TikTok (ATV, Barbosa e Mini).

Esta migration só SEMEIA as regras delas, como a 0366 fez com as outras:
`simular` nas lojas onde o Duoke manda hoje (pelo nome da integração, strip +
lower), `desligado` no resto. `INSERT … ON CONFLICT DO NOTHING`: regra que já
exista (criada pelo botão "simular nas lojas do Duoke") não muda. Nenhuma
tabela nem coluna nova. Com o motor desligado nada acontece; com ele ligado, o
motor só REGISTRA o que mandaria (nada sai: são só simulação).

O downgrade apaga as regras e as linhas do registro destas quatro automações.

Revision ID: 0371_atendimento_automacoes_simulacao
Revises: 0370_denuncia_robo_agenda_replica_19h
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0371_atendimento_automacoes_simulacao"
down_revision: str | None = "0370_denuncia_robo_agenda_replica_19h"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
REGRAS = "atendimento_automacao_regras"
REGISTROS = "atendimento_automacao_registros"
# As automações que ESTA migration semeia (congeladas, como as da 0366).
CODIGOS = (
    "shopee_nao_pago",
    "shopee_avaliacao_boa",
    "shopee_avaliacao_ruim",
    "tiktok_pedido_recebido",
)


def _semear() -> int:
    """As regras das automações novas em cada loja da plataforma delas; devolve quantas."""
    from app.services.atendimento import automacoes_catalogo as cat

    conn = op.get_bind()
    lojas = conn.execute(
        sa.text(
            f'SELECT id, platform::text, name FROM "{SCHEMA}".integrations '  # noqa: S608
            "WHERE archived_at IS NULL AND platform::text IN ('shopee', 'tiktok', 'ml')"
        )
    ).all()
    inserir = sa.text(
        f'INSERT INTO "{SCHEMA}".{REGRAS} '  # noqa: S608 — nomes fixos daqui
        "(id, automacao, integration_id, plataforma, modo, ligada_desde, partes, atraso_min, "
        "janela_inicio, janela_fim, condicoes) VALUES (gen_random_uuid(), :automacao, :integ, "
        ":plataforma, :modo, :ligada, "
        "CAST(:partes AS jsonb), :atraso, :ini, :fim, CAST(:condicoes AS jsonb)) "
        "ON CONFLICT (automacao, integration_id) DO NOTHING"
    )
    agora = datetime.now(UTC)
    n = 0
    for integ, plataforma, nome in lojas:
        for aut in cat.por_plataforma(cat.plataforma_da_integracao(plataforma)):
            if aut.codigo not in CODIGOS:
                continue
            r = cat.regra_semente(aut, nome)
            conn.execute(
                inserir,
                {
                    "automacao": r["automacao"],
                    "integ": integ,
                    "plataforma": r["plataforma"],
                    "modo": r["modo"],
                    "ligada": None if r["modo"] == cat.MODO_DESLIGADO else agora,
                    "partes": json.dumps(r["partes"], ensure_ascii=False),
                    "atraso": r["atraso_min"],
                    "ini": r["janela_inicio"],
                    "fim": r["janela_fim"],
                    "condicoes": json.dumps(r["condicoes"], ensure_ascii=False),
                },
            )
            n += 1
    return n


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    _semear()


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    codigos = ", ".join(f"'{c}'" for c in CODIGOS)
    op.execute(f'DELETE FROM "{SCHEMA}".{REGISTROS} WHERE automacao IN ({codigos})')  # noqa: S608
    op.execute(f'DELETE FROM "{SCHEMA}".{REGRAS} WHERE automacao IN ({codigos})')  # noqa: S608
