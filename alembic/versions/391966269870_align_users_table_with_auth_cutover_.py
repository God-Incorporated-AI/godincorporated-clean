"""align users table with auth cutover (seeker_id, display_name_lower, last_login)

Revision ID: 391966269870
Revises: a9c2f0c6d032
Create Date: 2026-02-23 11:44:30.345630

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '391966269870'
down_revision: Union[str, Sequence[str], None] = 'a9c2f0c6d032'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("seeker_id", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("display_name_lower", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("last_login", sa.DateTime(timezone=True), nullable=True))

    op.create_index("idx_users_seeker_id", "users", ["seeker_id"])
    op.create_index("idx_users_display_name_lower", "users", ["display_name_lower"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_users_display_name_lower", table_name="users")
    op.drop_index("idx_users_seeker_id", table_name="users")

    op.drop_column("users", "last_login")
    op.drop_column("users", "display_name_lower")
    op.drop_column("users", "seeker_id")
