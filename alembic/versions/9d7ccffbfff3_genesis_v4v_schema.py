def upgrade():
    from alembic import op
    with open("sql/0000_v4v_schema.sql", "r") as f:
        sql = f.read()
    op.execute(sql)
"""Genesis V4V schema

Revision ID: 9d7ccffbfff3
Revises: 
Create Date: 2026-02-03 11:39:51.663065

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d7ccffbfff3'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() : 
    from alembic import op
    with open("sql/0000_initial_canonical_schema.sql", "r") as f:
        sql = f.read()
    op.execute(sql)


def downgrade() :
    raise NotImplementedError("Genesis schema downgrade is not supported.")
