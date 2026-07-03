"""
Records and updates the status of every application in the database.

Will implement `record_application(job, cv_path, cover_letter_path, status)`,
`update_status(application_id, new_status)` (e.g. draft -> submitted ->
interview -> rejected/offer), and query helpers like
`get_applications_for_date(date)` used by reports.daily_report. Kept separate
from application_submitter.py: tracking state is not the same concern as
actually submitting an application on a site.
"""
