"""add_university_wallet_credits

Revision ID: 1c34256633b2
Revises: 20251204_add_daily_coach
Create Date: 2025-12-19 20:14:52.912594

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1c34256633b2'
down_revision: Union[str, Sequence[str], None] = '20251204_add_daily_coach'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



def upgrade():
    """Apply migration: Add new tables and backfill data."""
    
    # -------------------------------------------------------------------------
    # 1. CREATE: UniversityWallet Table
    # -------------------------------------------------------------------------
    print("Creating university_wallet table...")
    op.create_table(
        'university_wallet',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('university_id', sa.Integer(), nullable=False),
        sa.Column('silver_balance', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('gold_balance', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('silver_annual_cap', sa.Integer(), nullable=True),
        sa.Column('gold_annual_cap', sa.Integer(), nullable=True),
        sa.Column('renewal_date', sa.Date(), nullable=True),
        sa.Column('last_renewed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['university_id'], ['university.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('university_id', name='uq_university_wallet_university_id')
    )
    
    # Add indexes
    op.create_index('ix_university_wallet_university_id', 'university_wallet', ['university_id'])
    print("✓ university_wallet table created")
    
    # -------------------------------------------------------------------------
    # 2. CREATE: CreditTransaction Table
    # -------------------------------------------------------------------------
    print("Creating credit_transaction table...")
    op.create_table(
        'credit_transaction',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('university_id', sa.Integer(), nullable=True),
        sa.Column('feature', sa.String(64), nullable=False),
        sa.Column('currency', sa.String(16), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('tx_type', sa.String(32), nullable=False),
        sa.Column('wallet_type', sa.String(32), nullable=False, server_default='personal'),
        sa.Column('before_balance', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('after_balance', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('run_id', sa.String(128), nullable=True),
        sa.Column('status', sa.String(32), nullable=False, server_default='completed'),
        sa.Column('meta_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['university_id'], ['university.id'], ondelete='SET NULL'),
    )
    
    # Add indexes for performance
    op.create_index('ix_credit_transaction_user_id', 'credit_transaction', ['user_id'])
    op.create_index('ix_credit_transaction_university_id', 'credit_transaction', ['university_id'])
    op.create_index('ix_credit_transaction_feature', 'credit_transaction', ['feature'])
    op.create_index('ix_credit_transaction_tx_type', 'credit_transaction', ['tx_type'])
    op.create_index('ix_credit_transaction_run_id', 'credit_transaction', ['run_id'])
    op.create_index('ix_credit_transaction_created_at', 'credit_transaction', ['created_at'])
    print("✓ credit_transaction table created")
    
    # -------------------------------------------------------------------------
    # 3. BACKFILL: Create wallets for existing universities
    # -------------------------------------------------------------------------
    print("Backfilling wallets for existing universities...")
    
    # Calculate renewal date (1 year from now)
    renewal_date = date.today() + relativedelta(years=1)
    
    # Insert wallet for each existing university
    # Default: 10,000 silver credits, 5,000 gold credits
    op.execute(f"""
        INSERT INTO university_wallet (
            university_id,
            silver_balance,
            gold_balance,
            silver_annual_cap,
            gold_annual_cap,
            renewal_date,
            created_at,
            updated_at
        )
        SELECT 
            id,
            10000,  -- Initial silver balance
            5000,   -- Initial gold balance
            10000,  -- Silver annual cap
            5000,   -- Gold annual cap
            '{renewal_date}',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM university
        WHERE NOT EXISTS (
            SELECT 1 FROM university_wallet 
            WHERE university_wallet.university_id = university.id
        )
    """)
    
    print("✓ Wallets backfilled for existing universities")
    
    print("\n" + "="*70)
    print("MIGRATION COMPLETE: UniversityWallet and CreditTransaction added")
    print("="*70)


def downgrade():
    """Rollback migration: Remove new tables."""
    
    print("Rolling back migration...")
    
    # Drop indexes first
    print("Dropping indexes...")
    op.drop_index('ix_credit_transaction_created_at', 'credit_transaction')
    op.drop_index('ix_credit_transaction_run_id', 'credit_transaction')
    op.drop_index('ix_credit_transaction_tx_type', 'credit_transaction')
    op.drop_index('ix_credit_transaction_feature', 'credit_transaction')
    op.drop_index('ix_credit_transaction_university_id', 'credit_transaction')
    op.drop_index('ix_credit_transaction_user_id', 'credit_transaction')
    op.drop_index('ix_university_wallet_university_id', 'university_wallet')
    
    # Drop tables
    print("Dropping tables...")
    op.drop_table('credit_transaction')
    op.drop_table('university_wallet')
    
    print("✓ Migration rolled back")