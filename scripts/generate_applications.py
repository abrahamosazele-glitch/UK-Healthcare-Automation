"""
CLI entry point to generate CVs/cover letters for new, unapplied jobs.

Will query database.db_manager for jobs without an existing Application row,
run ai.cv_generator + ai.cover_letter_generator for each, and record them via
applications.application_tracker.record_application().
"""
