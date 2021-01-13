"""new monitored sub options

Revision ID: 380abce0d196
Revises: 279f1e8d64eb
Create Date: 2021-01-13 14:39:46.631940

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '380abce0d196'
down_revision = '279f1e8d64eb'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('reddit_monitored_sub', sa.Column('comment_on_oc', sa.Boolean(), nullable=True))
    op.add_column('reddit_monitored_sub', sa.Column('comment_on_repost', sa.Boolean(), nullable=True))
    op.add_column('reddit_monitored_sub', sa.Column('lock_response_comment', sa.Boolean(), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('reddit_monitored_sub', 'lock_response_comment')
    op.drop_column('reddit_monitored_sub', 'comment_on_repost')
    op.drop_column('reddit_monitored_sub', 'comment_on_oc')
    # ### end Alembic commands ###
