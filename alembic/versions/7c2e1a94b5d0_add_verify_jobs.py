"""add verify_jobs (twin-verifier split)

Revision ID: 7c2e1a94b5d0
Revises: 3f515989a9f9
Create Date: 2026-11-23

Phase 3 twin-verifier extraction (contracts README §5.2): the
in-memory ``_JOBS`` dict in ``backend/api/routes/verify.py`` is
replaced by this Postgres table, so a verify job survives a restart
and the Celery worker runs it from the row.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c2e1a94b5d0'
down_revision: Union[str, Sequence[str], None] = '3f515989a9f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('verify_jobs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('twin_id', sa.String(), nullable=True),
    sa.Column('revision_id', sa.String(), nullable=True),
    sa.Column('session_id', sa.String(), nullable=True),
    sa.Column('idempotency_key', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('request', sa.JSON(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('counts', sa.JSON(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_verify_jobs_revision_id'), 'verify_jobs', ['revision_id'], unique=False)
    op.create_index(op.f('ix_verify_jobs_idempotency_key'), 'verify_jobs', ['idempotency_key'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_verify_jobs_idempotency_key'), table_name='verify_jobs')
    op.drop_index(op.f('ix_verify_jobs_revision_id'), table_name='verify_jobs')
    op.drop_table('verify_jobs')
