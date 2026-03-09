"""add patients table and conversation patient link

Revision ID: 2f8c1a9d4b10
Revises: 9d7a6b3f41c2
Create Date: 2026-01-26 21:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2f8c1a9d4b10"
down_revision: Union[str, Sequence[str], None] = "9d7a6b3f41c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    if not _table_exists(inspector, "patients"):
        op.create_table(
            "patients",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("identifier", sa.String(length=64), nullable=False),
            sa.Column("owner_user_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_user_id", "identifier", name="uq_patients_owner_identifier"),
        )
        inspector = sa.inspect(bind)

    idx_pat_identifier = op.f("ix_patients_identifier")
    if not _has_index(inspector, "patients", idx_pat_identifier):
        op.create_index(idx_pat_identifier, "patients", ["identifier"], unique=False)

    idx_pat_owner = op.f("ix_patients_owner_user_id")
    if not _has_index(inspector, "patients", idx_pat_owner):
        op.create_index(idx_pat_owner, "patients", ["owner_user_id"], unique=False)

    if not _has_column(inspector, "conversations", "patient_id"):
        op.add_column("conversations", sa.Column("patient_id", sa.Integer(), nullable=True))
        inspector = sa.inspect(bind)

    idx_conv_patient = op.f("ix_conversations_patient_id")
    if not _has_index(inspector, "conversations", idx_conv_patient):
        op.create_index(idx_conv_patient, "conversations", ["patient_id"], unique=False)

    # SQLite cannot add FK constraints via ALTER TABLE; skip FK there.
    if dialect != "sqlite":
        fks = inspector.get_foreign_keys("conversations")
        has_fk = any(
            fk.get("referred_table") == "patients" and "patient_id" in (fk.get("constrained_columns") or [])
            for fk in fks
        )
        if not has_fk:
            op.create_foreign_key("fk_conversations_patient_id_patients", "conversations", "patients", ["patient_id"], ["id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    if dialect != "sqlite":
        fks = inspector.get_foreign_keys("conversations")
        if any(fk.get("name") == "fk_conversations_patient_id_patients" for fk in fks):
            op.drop_constraint("fk_conversations_patient_id_patients", "conversations", type_="foreignkey")

    idx_conv_patient = op.f("ix_conversations_patient_id")
    if _has_index(inspector, "conversations", idx_conv_patient):
        op.drop_index(idx_conv_patient, table_name="conversations")

    if _has_column(inspector, "conversations", "patient_id"):
        op.drop_column("conversations", "patient_id")

    inspector = sa.inspect(bind)
    if not _table_exists(inspector, "patients"):
        return

    idx_pat_owner = op.f("ix_patients_owner_user_id")
    if _has_index(inspector, "patients", idx_pat_owner):
        op.drop_index(idx_pat_owner, table_name="patients")

    idx_pat_identifier = op.f("ix_patients_identifier")
    if _has_index(inspector, "patients", idx_pat_identifier):
        op.drop_index(idx_pat_identifier, table_name="patients")

    op.drop_table("patients")
