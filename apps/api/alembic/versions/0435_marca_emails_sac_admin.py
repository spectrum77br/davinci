"""marca_emails — só sac@ e admin@ (saem duvidas@ e atacado@)

Eduardo, 27–28/10/2026: cada site tem só dois endereços, sac@ e admin@ ("oq n
for do sac cai em admin"); duvidas@ e atacado@ ficam esquecidos, e a
assinatura de Cadastros › E-mails passa a ter só SAC e Admin. Só dados (a
coluna `tipo` é texto livre, sem CHECK):

  • para cada marca com `duvidas`, nasce `admin` = admin@ + o domínio do
    duvidas@ (se a marca ainda não tiver admin);
  • as linhas `duvidas` e `atacado` saem.

Em produção, em 28/10, só a Uranyx tinha linhas (sac@, duvidas@, atacado@):
fica sac@uranyx.com.br + admin@uranyx.com.br — o admin@ já é alias da caixa
"Principal — Tuta". O roteamento do e-mail que chega não muda: duvidas@ e
atacado@ continuam caixa de site pelo nome (`LOCAIS_DE_SITE`), e o admin@
cadastrado não entra na rota pelo cadastro (`rotear.cadastro` o pula: ele
segue caixa de site pelo nome, com as travas de antes).

O downgrade refaz duvidas@ e atacado@ com o domínio do admin@ e tira o admin.

Revision ID: 0435_marca_emails_sac_admin
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0435_marca_emails_sac_admin"
down_revision: str | None = "0434_garantia_automatica"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO davinci.marca_emails (id, marca_id, tipo, email)
        SELECT gen_random_uuid(), marca_id, 'admin', 'admin@' || split_part(email, '@', 2)
          FROM davinci.marca_emails
         WHERE tipo = 'duvidas' AND split_part(email, '@', 2) <> ''
        ON CONFLICT ON CONSTRAINT uq_marca_emails_tipo DO NOTHING
        """
    )
    op.execute("DELETE FROM davinci.marca_emails WHERE tipo IN ('duvidas', 'atacado')")


def downgrade() -> None:
    op.execute(
        """
        INSERT INTO davinci.marca_emails (id, marca_id, tipo, email)
        SELECT gen_random_uuid(), marca_id, t.tipo, t.tipo || '@' || split_part(email, '@', 2)
          FROM davinci.marca_emails
         CROSS JOIN (VALUES ('duvidas'), ('atacado')) AS t(tipo)
         WHERE marca_emails.tipo = 'admin' AND split_part(email, '@', 2) <> ''
        ON CONFLICT ON CONSTRAINT uq_marca_emails_tipo DO NOTHING
        """
    )
    op.execute("DELETE FROM davinci.marca_emails WHERE tipo = 'admin'")
