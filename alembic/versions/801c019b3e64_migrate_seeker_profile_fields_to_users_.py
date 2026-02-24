"""migrate seeker profile fields to users (title, donation_total, influence_state, eligibility_flags)

Revision ID: 801c019b3e64
Revises: 391966269870
Create Date: 2026-02-24 08:24:54.359314

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '801c019b3e64'
down_revision: Union[str, Sequence[str], None] = '391966269870'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("title", sa.Text(), nullable=False, server_default="Seeker"))
    op.add_column("users", sa.Column("donation_total", sa.Numeric(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("influence_state", sa.Text(), nullable=False, server_default="disabled"))
    op.add_column("users", sa.Column("eligibility_flags", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.add_column("users", sa.Column("scroll_count", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("idx_users_influence_state", "users", ["influence_state"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_users_influence_state", table_name="users")
    op.drop_column("users", "scroll_count")
    op.drop_column("users", "eligibility_flags")
    op.drop_column("users", "influence_state")
    op.drop_column("users", "donation_total")
    op.drop_column("users", "title")
