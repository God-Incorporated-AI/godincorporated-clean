"""Genesis V4V schema

Revision ID: 9d7ccffbfff3
Revises: 
Create Date: 2026-02-03 11:39:51.663065

"""
from pathlib import Path
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '9d7ccffbfff3'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


GENESIS_SQL_PATH = (
    Path(__file__).resolve().parents[2]
    / "sql"
    / "archive"
    / "0000_initial_canonical_schema_genesis_2026_02_06.sql"
)


def upgrade() -> None:
    sql = GENESIS_SQL_PATH.read_text(
        encoding="utf-8"
    )
    op.execute(sql)


def downgrade() :
    raise NotImplementedError("Genesis schema downgrade is not supported.")
