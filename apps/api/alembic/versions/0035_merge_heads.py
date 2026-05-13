"""merge approval + marketplaces heads

Revision ID: 41b24a0efd04
Revises: 0033_companies_enabled_marketplaces, 0034_margens_margem_min
Create Date: 2026-05-13 15:28:18.262807

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0035_merge_heads'
down_revision: Union[str, None] = ('0033_companies_enabled_marketplaces', '0034_margens_margem_min')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
