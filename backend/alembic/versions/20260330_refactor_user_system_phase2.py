"""Refactor user system to global Identities - Phase 2 (Consolidated & Idempotent)
 
Revision ID: 440261f5594f
Revises: add_agent_credentials
Create Date: 2026-03-30
"""
import uuid
from typing import Sequence, Union
 
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect
 
# revision identifiers, used by Alembic.
revision: str = '440261f5594f'
down_revision: Union[str, None] = 'add_agent_credentials'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
 
 
def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()
 
    # 1. Baseline: Add missing/intermediate columns to users (idempotent)
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT True")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()")
    
    # 2. Cleanup: Drop obsolete SSO columns
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS feishu_user_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS wecom_user_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS dingtalk_user_id")
 
    # 3. Create identities table if not exists
    if 'identities' not in tables:
        op.create_table(
            'identities',
            sa.Column('id', sa.UUID(), nullable=False),
            sa.Column('email', sa.String(length=255), nullable=True),
            sa.Column('phone', sa.String(length=50), nullable=True),
            sa.Column('username', sa.String(length=100), nullable=True),
            sa.Column('password_hash', sa.String(length=255), nullable=True),
            sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
            sa.Column('is_platform_admin', sa.Boolean(), server_default='false', nullable=False),
            sa.Column('email_verified', sa.Boolean(), server_default='false', nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_identities_email'), 'identities', ['email'], unique=True)
        op.create_index(op.f('ix_identities_phone'), 'identities', ['phone'], unique=True)
        op.create_index(op.f('ix_identities_username'), 'identities', ['username'], unique=True)
 
    # 4. Add identity_id to users if not exists
    user_columns = [c['name'] for c in inspector.get_columns('users')]
    if 'identity_id' not in user_columns:
        op.add_column('users', sa.Column('identity_id', sa.UUID(), nullable=True))
        op.create_index(op.f('ix_users_identity_id'), 'users', ['identity_id'], unique=False)
        op.create_foreign_key('fk_users_identity_id', 'users', 'identities', ['identity_id'], ['id'])
 
    # 5. Data migration (idempotent)
    # Only migrate users that don't have an identity_id yet AND if old columns exist
    user_columns = [c['name'] for c in inspector.get_columns('users')]
    if all(col in user_columns for col in ['email', 'primary_mobile', 'username']):
        result = conn.execute(sa.text("""
            SELECT id, email, primary_mobile, username, password_hash, email_verified, is_active, role 
            FROM users 
            WHERE identity_id IS NULL
        """))
        users_data = result.fetchall()
    else:
        users_data = []
    
    if users_data:
        # Load existing identities to match against
        ident_res = conn.execute(sa.text("SELECT id, email, phone, username FROM identities"))
        existing_idents = ident_res.fetchall()
        
        # Build map: (type, val) -> identity_id
        identity_map = {}
        for r in existing_idents:
            if r[1]: identity_map[f"e:{r[1]}"] = r[0]
            if r[2]: identity_map[f"p:{r[2]}"] = r[0]
            if r[3]: identity_map[f"u:{r[3]}"] = r[0]
 
        for row in users_data:
            u_id, u_email, u_phone, u_username, u_pwd, u_email_verified, u_active, u_role = row
            
            # Check if this person already has an identity
            found_id = None
            if u_email and f"e:{u_email}" in identity_map: found_id = identity_map[f"e:{u_email}"]
            elif u_phone and f"p:{u_phone}" in identity_map: found_id = identity_map[f"p:{u_phone}"]
            elif u_username and f"u:{u_username}" in identity_map: found_id = identity_map[f"u:{u_username}"]
            
            if not found_id:
                # Create new identity
                found_id = str(uuid.uuid4())
                is_platform_admin = (u_role == 'platform_admin')
                
                conn.execute(sa.text("""
                    INSERT INTO identities (id, email, phone, username, password_hash, email_verified, is_active, is_platform_admin)
                    VALUES (:id, :email, :phone, :username, :password_hash, :email_verified, :is_active, :admin)
                """), {
                    "id": found_id,
                    "email": u_email,
                    "phone": u_phone,
                    "username": u_username,
                    "password_hash": u_pwd,
                    "email_verified": u_email_verified if u_email_verified is not None else False,
                    "is_active": u_active if u_active is not None else True,
                    "admin": is_platform_admin
                })
                # Update map to prevent duplicates in this loop
                if u_email: identity_map[f"e:{u_email}"] = found_id
                if u_phone: identity_map[f"p:{u_phone}"] = found_id
                if u_username: identity_map[f"u:{u_username}"] = found_id
            
            # Update user
            conn.execute(sa.text("UPDATE users SET identity_id = :identity_id WHERE id = :user_id"), {
                "identity_id": found_id,
                "user_id": u_id
            })
 
    # 6. Cleanup: Make username/email nullable and DROP redundant columns
    current_columns = [c['name'] for c in inspector.get_columns('users')]
    if 'username' in current_columns:
        op.alter_column('users', 'username', existing_type=sa.String(length=100), nullable=True)
    if 'email' in current_columns:
        op.alter_column('users', 'email', existing_type=sa.String(length=255), nullable=True)

    # Physically drop redundant columns
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS username")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS email")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS password_hash")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS email_verified")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS primary_mobile")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS primary_email")
 
 
def downgrade() -> None:
    # Cleanup identity linking
    op.drop_constraint('fk_users_identity_id', 'users', type_='foreignkey')
    op.drop_index(op.f('ix_users_identity_id'), table_name='users')
    op.drop_column('users', 'identity_id')
    
    op.drop_index(op.f('ix_identities_username'), table_name='identities')
    op.drop_index(op.f('ix_identities_phone'), table_name='identities')
    op.drop_index(op.f('ix_identities_email'), table_name='identities')
    op.drop_table('identities')
