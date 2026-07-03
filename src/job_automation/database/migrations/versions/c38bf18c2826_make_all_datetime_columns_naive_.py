"""make all datetime columns naive (postgres compatibility)

Every `DateTime(timezone=True)` column in this schema is changed to plain
`DateTime()` (`TIMESTAMP WITHOUT TIME ZONE` on Postgres). This app's entire
timestamp convention has always been naive UTC (`utils.helpers.utc_now()`,
originally documented as a SQLite-specific workaround), but the columns
themselves were declared `timezone=True` — harmless on SQLite (which
ignores the flag and always returns naive values), but on Postgres a
`TIMESTAMP WITH TIME ZONE` column returns a timezone-*aware* `datetime`
from psycopg2. Comparing that against this app's naive `utc_now()`
anywhere (scheduler task history, closing-soon-job checks, analytics date
bucketing, ...) would raise `TypeError: can't subtract offset-naive and
offset-aware datetimes` the moment this app runs against Postgres instead
of SQLite. See `database/mixins.py`'s `TimestampMixin` docstring for the
full explanation. Caught during the Production Readiness milestone's
PostgreSQL compatibility review, before any real production database
existed — this migration brings the schema in line with the now-corrected
model declarations for anyone running `alembic upgrade head` from scratch.

Revision ID: c38bf18c2826
Revises: 0c09fb52cf5c
Create Date: 2026-07-03 13:06:11.307590

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c38bf18c2826'
down_revision: Union[str, Sequence[str], None] = '0c09fb52cf5c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('activity_logs') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('application_workflows') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('applications') as batch_op:
        batch_op.alter_column('applied_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('candidate_profiles') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('certificates') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('cover_letters') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('cvs') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('employer_activity_log') as batch_op:
        batch_op.alter_column('occurred_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('employer_contacts') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('employer_departments') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('employer_profiles') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('employers') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('generated_documents') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('interview_checklist_items') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('interview_notes') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('interview_records') as batch_op:
        batch_op.alter_column('scheduled_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('interview_reminders') as batch_op:
        batch_op.alter_column('remind_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('interviews') as batch_op:
        batch_op.alter_column('scheduled_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('job_alerts') as batch_op:
        batch_op.alter_column('last_run_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('job_matches') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('job_reminders') as batch_op:
        batch_op.alter_column('remind_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('jobs') as batch_op:
        batch_op.alter_column('posted_date', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=True)
        batch_op.alter_column('closing_soon_notified_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('notifications') as batch_op:
        batch_op.alter_column('read_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('saved_jobs') as batch_op:
        batch_op.alter_column('interview_date', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('scheduler_task_runs') as batch_op:
        batch_op.alter_column('started_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('finished_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('scraper_runs') as batch_op:
        batch_op.alter_column('started_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('finished_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('users') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('workflow_audit_logs') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('workflow_status_history') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(), existing_nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('activity_logs') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('application_workflows') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('applications') as batch_op:
        batch_op.alter_column('applied_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('candidate_profiles') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('certificates') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('cover_letters') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('cvs') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('employer_activity_log') as batch_op:
        batch_op.alter_column('occurred_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('employer_contacts') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('employer_departments') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('employer_profiles') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('employers') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('generated_documents') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('interview_checklist_items') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('interview_notes') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('interview_records') as batch_op:
        batch_op.alter_column('scheduled_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('interview_reminders') as batch_op:
        batch_op.alter_column('remind_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('interviews') as batch_op:
        batch_op.alter_column('scheduled_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('job_alerts') as batch_op:
        batch_op.alter_column('last_run_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('job_matches') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('job_reminders') as batch_op:
        batch_op.alter_column('remind_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('jobs') as batch_op:
        batch_op.alter_column('posted_date', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=True)
        batch_op.alter_column('closing_soon_notified_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('notifications') as batch_op:
        batch_op.alter_column('read_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('saved_jobs') as batch_op:
        batch_op.alter_column('interview_date', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('scheduler_task_runs') as batch_op:
        batch_op.alter_column('started_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('finished_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('scraper_runs') as batch_op:
        batch_op.alter_column('started_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('finished_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=True)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('users') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('workflow_audit_logs') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)

    with op.batch_alter_table('workflow_status_history') as batch_op:
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True), existing_nullable=False)
