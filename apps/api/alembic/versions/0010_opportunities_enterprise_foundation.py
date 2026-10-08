"""opportunities enterprise foundation

Revision ID: 0010_opps_enterprise
Revises: 0009_contacts_segmentation
Create Date: 2026-10-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_opps_enterprise"

down_revision: Union[str, Sequence[str], None] = (
    "0009_contacts_segmentation"
)

branch_labels: Union[str, Sequence[str], None] = None

depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------------------
    # ENTERPRISE OPPORTUNITY CLASSIFICATION
    # ---------------------------------------------------------

    op.add_column(
        "opportunities",
        sa.Column(
            "opportunity_type",
            sa.String(length=50),
            server_default="new_business",
            nullable=False,
        ),
    )

    op.add_column(
        "opportunities",
        sa.Column(
            "lead_source",
            sa.String(length=80),
            nullable=True,
        ),
    )

    op.add_column(
        "opportunities",
        sa.Column(
            "priority",
            sa.String(length=20),
            server_default="medium",
            nullable=False,
        ),
    )

    op.add_column(
        "opportunities",
        sa.Column(
            "forecast_category",
            sa.String(length=30),
            server_default="pipeline",
            nullable=False,
        ),
    )

    op.add_column(
        "opportunities",
        sa.Column(
            "next_step",
            sa.Text(),
            nullable=True,
        ),
    )

    op.add_column(
        "opportunities",
        sa.Column(
            "loss_reason",
            sa.Text(),
            nullable=True,
        ),
    )

    op.add_column(
        "opportunities",
        sa.Column(
            "competitor",
            sa.String(length=250),
            nullable=True,
        ),
    )

    op.add_column(
        "opportunities",
        sa.Column(
            "stage_entered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ---------------------------------------------------------
    # VALIDATION CONSTRAINTS
    # ---------------------------------------------------------

    op.create_check_constraint(
        "ck_opportunities_priority",
        "opportunities",
        "priority IN ('low', 'medium', 'high', 'critical')",
    )

    op.create_check_constraint(
        "ck_opportunities_forecast_category",
        "opportunities",
        (
            "forecast_category IN "
            "('pipeline', 'best_case', 'commit', 'closed', 'omitted')"
        ),
    )

    op.create_check_constraint(
        "ck_opportunities_type",
        "opportunities",
        (
            "opportunity_type IN "
            "('new_business', 'renewal', 'upsell', 'cross_sell', 'expansion')"
        ),
    )

    # ---------------------------------------------------------
    # INDEXES
    # ---------------------------------------------------------

    op.create_index(
        "ix_opportunities_opportunity_type",
        "opportunities",
        ["opportunity_type"],
    )

    op.create_index(
        "ix_opportunities_lead_source",
        "opportunities",
        ["lead_source"],
    )

    op.create_index(
        "ix_opportunities_priority",
        "opportunities",
        ["priority"],
    )

    op.create_index(
        "ix_opportunities_forecast_category",
        "opportunities",
        ["forecast_category"],
    )

    op.create_index(
        "ix_opportunities_stage_entered_at",
        "opportunities",
        ["stage_entered_at"],
    )

    # ---------------------------------------------------------
    # OPPORTUNITY STAGE HISTORY
    # ---------------------------------------------------------

    op.create_table(
        "opportunity_stage_history",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "opportunity_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "pipeline_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "from_stage_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "to_stage_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "changed_by_user_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "change_reason",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_id"],
            ["pipelines.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["from_stage_id"],
            ["pipeline_stages.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["to_stage_id"],
            ["pipeline_stages.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_opportunity_stage_history_organization_id",
        "opportunity_stage_history",
        ["organization_id"],
    )

    op.create_index(
        "ix_opportunity_stage_history_opportunity_id",
        "opportunity_stage_history",
        ["opportunity_id"],
    )

    op.create_index(
        "ix_opportunity_stage_history_pipeline_id",
        "opportunity_stage_history",
        ["pipeline_id"],
    )

    op.create_index(
        "ix_opportunity_stage_history_to_stage_id",
        "opportunity_stage_history",
        ["to_stage_id"],
    )

    op.create_index(
        "ix_opportunity_stage_history_changed_at",
        "opportunity_stage_history",
        ["changed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_opportunity_stage_history_changed_at",
        table_name="opportunity_stage_history",
    )

    op.drop_index(
        "ix_opportunity_stage_history_to_stage_id",
        table_name="opportunity_stage_history",
    )

    op.drop_index(
        "ix_opportunity_stage_history_pipeline_id",
        table_name="opportunity_stage_history",
    )

    op.drop_index(
        "ix_opportunity_stage_history_opportunity_id",
        table_name="opportunity_stage_history",
    )

    op.drop_index(
        "ix_opportunity_stage_history_organization_id",
        table_name="opportunity_stage_history",
    )

    op.drop_table(
        "opportunity_stage_history"
    )

    op.drop_index(
        "ix_opportunities_stage_entered_at",
        table_name="opportunities",
    )

    op.drop_index(
        "ix_opportunities_forecast_category",
        table_name="opportunities",
    )

    op.drop_index(
        "ix_opportunities_priority",
        table_name="opportunities",
    )

    op.drop_index(
        "ix_opportunities_lead_source",
        table_name="opportunities",
    )

    op.drop_index(
        "ix_opportunities_opportunity_type",
        table_name="opportunities",
    )

    op.drop_constraint(
        "ck_opportunities_type",
        "opportunities",
        type_="check",
    )

    op.drop_constraint(
        "ck_opportunities_forecast_category",
        "opportunities",
        type_="check",
    )

    op.drop_constraint(
        "ck_opportunities_priority",
        "opportunities",
        type_="check",
    )

    op.drop_column(
        "opportunities",
        "stage_entered_at",
    )

    op.drop_column(
        "opportunities",
        "competitor",
    )

    op.drop_column(
        "opportunities",
        "loss_reason",
    )

    op.drop_column(
        "opportunities",
        "next_step",
    )

    op.drop_column(
        "opportunities",
        "forecast_category",
    )

    op.drop_column(
        "opportunities",
        "priority",
    )

    op.drop_column(
        "opportunities",
        "lead_source",
    )

    op.drop_column(
        "opportunities",
        "opportunity_type",
    )