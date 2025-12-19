"""add daily coach session and task models

Revision ID: 20251204_add_daily_coach
Revises: 70b0d1a0641e
Create Date: 2025-12-04 00:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20251204_add_daily_coach"
down_revision: Union[str, Sequence[str], None] = "70b0d1a0641e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create daily_coach_session table
    op.create_table(
        "daily_coach_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "path_type",
            sa.String(length=20),
            nullable=False,
            server_default="job",
        ),
        sa.Column(
            "plan_digest",
            sa.String(length=128),
            nullable=True,
            comment="Dream Planner meta.inputs_digest used for this session",
        ),
        sa.Column(
            "plan_title",
            sa.String(length=255),
            nullable=True,
            comment="Short label like 'Dream Job: Data Analyst, 12–24 LPA'",
        ),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column(
            "day_index",
            sa.Integer(),
            nullable=True,
            comment="Day number in the 30–60–90 / MVP timeline (if applicable)",
        ),
        sa.Column(
            "ai_note",
            sa.Text(),
            nullable=True,
            comment="Short AI note for this day (anchor / focus)",
        ),
        sa.Column(
            "reflection",
            sa.Text(),
            nullable=True,
            comment="User-written reflection for this day",
        ),
        sa.Column(
            "is_closed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="True when the user marks the day as done",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_daily_coach_session_user_id",
            ondelete="CASCADE",
        ),
    )

    op.create_index(
        "ix_daily_coach_session_user_id",
        "daily_coach_session",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_daily_coach_session_path_type",
        "daily_coach_session",
        ["path_type"],
        unique=False,
    )
    op.create_index(
        "ix_daily_coach_session_plan_digest",
        "daily_coach_session",
        ["plan_digest"],
        unique=False,
    )
    op.create_index(
        "ix_daily_coach_session_session_date",
        "daily_coach_session",
        ["session_date"],
        unique=False,
    )

    # Create daily_coach_task table
    op.create_table(
        "daily_coach_task",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column(
            "title",
            sa.String(length=255),
            nullable=False,
            comment="Short, actionable task label",
        ),
        sa.Column(
            "detail",
            sa.Text(),
            nullable=True,
            comment="Optional details / how-to for the task",
        ),
        sa.Column(
            "category",
            sa.String(length=64),
            nullable=True,
            comment="Optional tag: e.g. 'skills', 'projects', 'resume', 'networking'",
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=True,
            comment="For ordering tasks within the session",
        ),
        sa.Column(
            "is_done",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["daily_coach_session.id"],
            name="fk_daily_coach_task_session_id",
            ondelete="CASCADE",
        ),
    )

    op.create_index(
        "ix_daily_coach_task_session_id",
        "daily_coach_task",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_daily_coach_task_is_done",
        "daily_coach_task",
        ["is_done"],
        unique=False,
    )


def downgrade() -> None:
    # Drop child table first (due to FK to session)
    op.drop_index("ix_daily_coach_task_is_done", table_name="daily_coach_task")
    op.drop_index("ix_daily_coach_task_session_id", table_name="daily_coach_task")
    op.drop_table("daily_coach_task")

    # Then drop session table + its indexes
    op.drop_index(
        "ix_daily_coach_session_session_date",
        table_name="daily_coach_session",
    )
    op.drop_index(
        "ix_daily_coach_session_plan_digest",
        table_name="daily_coach_session",
    )
    op.drop_index(
        "ix_daily_coach_session_path_type",
        table_name="daily_coach_session",
    )
    op.drop_index(
        "ix_daily_coach_session_user_id",
        table_name="daily_coach_session",
    )
    op.drop_table("daily_coach_session")
