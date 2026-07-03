"""
Optional automated submission step (kept separate and off by default).

Most healthcare job sites require manual submission or account-specific forms,
so this module is a placeholder for site-specific submission flows (e.g. via
Playwright form-filling) that can be enabled per-source later. Until then,
main.py generates documents and leaves status as "ready_to_apply" for manual
submission, and application_tracker.py records the outcome.
"""
