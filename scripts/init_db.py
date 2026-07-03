"""
CLI entry point to create database tables for local/dev use.

Normally schema changes go through Alembic migrations (`alembic upgrade
head`), not this script. This is a convenience for quickly spinning up a
throwaway dev database from the current models without a migration history.
"""

from job_automation.database.db_manager import init_db

if __name__ == "__main__":
    init_db()
    print("Database initialized from current models.")
