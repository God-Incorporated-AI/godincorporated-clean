"""extend users for authentication

Revision ID: a9c2f0c6d032
Revises: b10c410d2b87
Create Date: 2026-02-21 11:02:48.129454

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9c2f0c6d032'
down_revision: Union[str, Sequence[str], None] = 'b10c410d2b87'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("email", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("users", sa.Column("verification_token", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("reset_token", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("reset_token_expires_at", sa.DateTime(timezone=True), nullable=True))

    op.create_unique_constraint("users_email_key", "users", ["email"])
    op.create_index("idx_users_email", "users", ["email"])
    op.create_index("idx_users_verification_token", "users", ["verification_token"])
    op.create_index("idx_users_reset_token", "users", ["reset_token"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_users_reset_token", table_name="users")
    op.drop_index("idx_users_verification_token", table_name="users")
    op.drop_index("idx_users_email", table_name="users")
    op.drop_constraint("users_email_key", "users", type_="unique")

    op.drop_column("users", "reset_token_expires_at")
    op.drop_column("users", "reset_token")
    op.drop_column("users", "verification_token")
    op.drop_column("users", "email_verified")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "email")
