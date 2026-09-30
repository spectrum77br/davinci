# ruff: noqa: S608
"""IA de Chamado parada em "não sou robô": o aviso no Threema já nasce pro Cairo

30/09/2026 (297130): "se travar não sou robô me avisa … a mensagem pode vir
para mim cairo". Cria o cadastro `chamados_ia` (quem recebe o aviso da IA
parada em captcha/login — services/chamados_ia_aviso) com o Threema do Cairo:
primeiro o do cadastro de usuário (sa.geral@tutamail.com); sem isso, um
contato avulso com "Cairo" no nome. Não achou → cadastro vazio, e a aba
IA de Chamado › "quem recebe o aviso" resolve.

Downgrade apaga o cadastro.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0341_chamados_ia_aviso"
down_revision: str | None = "0340_nfse_nfeio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
CONTEXTO = "chamados_ia"
EMAIL_CAIRO = "sa.geral@tutamail.com"


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
    ids = _ids(
        bind.execute(
            sa.text(f"SELECT threema FROM {SCHEMA}.users WHERE lower(email) = :e"),
            {"e": EMAIL_CAIRO},
        ).scalar()
    )
    if not ids:
        # contatos avulsos: linha `diretorio`, formato "ID:Nome,ID:Nome"
        contatos = bind.execute(
            sa.text(
                f"SELECT recipients FROM {SCHEMA}.threema_informar_config "
                "WHERE contexto = 'diretorio'"
            )
        ).scalar()
        for item in (contatos or "").split(","):
            rid, _, nome = item.partition(":")
            if "cairo" in nome.lower() and rid.strip():
                ids = [rid.strip().upper()]
                break
    bind.execute(
        sa.text(
            f"INSERT INTO {SCHEMA}.threema_informar_config (id, contexto, recipients) "
            "VALUES (gen_random_uuid(), :c, :ids) ON CONFLICT (contexto) DO NOTHING"
        ),
        {"c": CONTEXTO, "ids": ",".join(ids)},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(f"DELETE FROM {SCHEMA}.threema_informar_config WHERE contexto = :c"),
        {"c": CONTEXTO},
    )
