# ruff: noqa: S608
"""Robô da Margem: o robô que age e o fiscal viram um só (um botão, uma lista)

Cairo, 25/09/2026: desligou o "Robô da Margem" na Ouvidoria pra testar e a
Margem continuou segurando — eram duas coisas (o auto-hold, com kill-switch
no .env, e o fiscal do painel). Agora o modo do painel manda nas duas e a
lista "Avisar" do robô é a única lista do Threema da Margem automática.

1. Junta as listas: quem recebia o aviso na hora (Informar da aba Margem,
   contexto `margem_auto`, com o link de aprovar pelo celular) + quem já
   estivesse no "Avisar" do robô → `ouvidoria_robos.threema_recipients` de
   `vigia_margem`. A linha `margem_auto` sai: o modal da Margem passa a ler e
   gravar direto na lista do robô (routers/informar.py).
2. Liga o robô: ele estava `desligado` (teste do Cairo em 22/09) e, com um
   botão só, desligado pararia de segurar pedido. Fica `ligado` — o que já
   acontecia de fato: o auto-hold segurava e avisava essa mesma lista.

Downgrade devolve a lista pra `margem_auto` (o modo fica como está).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0325_robo_margem_um_so"
down_revision: str | None = "0324_companies_ip"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
ROBO = "vigia_margem"
CONTEXTO = "margem_auto"
CARIMBO = "DaVinci (robô e fiscal da Margem juntos)"


def _ids(raw: str | None) -> list[str]:
    """Mesma regra de `threema.parse_recipients` (sem importar o app)."""
    if not raw:
        return []
    out: list[str] = []
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        rid = part.strip().upper()
        if rid and rid not in out:
            out.append(rid)
    return out


def upgrade() -> None:
    bind = op.get_bind()
    lista_margem = bind.execute(
        sa.text(f"SELECT recipients FROM {SCHEMA}.threema_informar_config WHERE contexto = :c"),
        {"c": CONTEXTO},
    ).scalar()
    robo = bind.execute(
        sa.text(f"SELECT threema_recipients, modo FROM {SCHEMA}.ouvidoria_robos WHERE chave = :k"),
        {"k": ROBO},
    ).first()

    juntos = _ids(robo.threema_recipients if robo else None)
    juntos += [rid for rid in _ids(lista_margem) if rid not in juntos]
    lista = ", ".join(juntos) or None

    if robo is None:
        # Banco em que a Ouvidoria nunca abriu: o catálogo completa o resto
        # (descrição, cadência, config) na primeira leitura da tela.
        bind.execute(
            sa.text(
                f"INSERT INTO {SCHEMA}.ouvidoria_robos "
                "(chave, nome, modo, threema_recipients) "
                "VALUES (:k, 'Robô da Margem', 'ligado', :lista)"
            ),
            {"k": ROBO, "lista": lista},
        )
    else:
        bind.execute(
            sa.text(
                f"UPDATE {SCHEMA}.ouvidoria_robos SET threema_recipients = :lista "
                "WHERE chave = :k"
            ),
            {"k": ROBO, "lista": lista},
        )
        if robo.modo != "ligado":
            bind.execute(
                sa.text(
                    f"UPDATE {SCHEMA}.ouvidoria_robos "
                    "SET modo = 'ligado', modo_alterado_por = :por, modo_alterado_em = now() "
                    "WHERE chave = :k"
                ),
                {"k": ROBO, "por": CARIMBO},
            )
    bind.execute(
        sa.text(f"DELETE FROM {SCHEMA}.threema_informar_config WHERE contexto = :c"),
        {"c": CONTEXTO},
    )


def downgrade() -> None:
    bind = op.get_bind()
    lista = bind.execute(
        sa.text(f"SELECT threema_recipients FROM {SCHEMA}.ouvidoria_robos WHERE chave = :k"),
        {"k": ROBO},
    ).scalar()
    ids = ",".join(_ids(lista))
    if not ids:
        return
    bind.execute(
        sa.text(
            f"INSERT INTO {SCHEMA}.threema_informar_config (id, contexto, recipients) "
            "VALUES (gen_random_uuid(), :c, :ids) "
            "ON CONFLICT (contexto) DO UPDATE SET recipients = EXCLUDED.recipients"
        ),
        {"c": CONTEXTO, "ids": ids},
    )
