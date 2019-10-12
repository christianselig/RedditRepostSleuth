"""add index build table

Revision ID: e5dc3cc2e2f1
Revises: 183f811bafe5
Create Date: 2019-09-09 07:23:21.647702

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5dc3cc2e2f1'
down_revision = '183f811bafe5'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('index_build_times',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('index_type', sa.String(length=50), nullable=False),
    sa.Column('hostname', sa.String(length=200), nullable=False),
    sa.Column('items', sa.Integer(), nullable=False),
    sa.Column('build_start', sa.DateTime(), nullable=False),
    sa.Column('build_end', sa.DateTime(), nullable=False),
    sa.Column('build_minutes', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('index_build_times')
    # ### end Alembic commands ###
