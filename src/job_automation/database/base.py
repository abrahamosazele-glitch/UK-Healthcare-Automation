"""
Declarative base for all SQLAlchemy ORM models.

Kept in its own module (rather than in models.py) so Alembic's env.py can
import `Base.metadata` as `target_metadata` without also importing the model
classes themselves — avoiding circular imports once models.py starts
defining tables (Job, CandidateProfile, Application, DailyReportLog).
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
