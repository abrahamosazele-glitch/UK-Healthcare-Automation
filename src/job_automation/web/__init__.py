"""
The web dashboard: a FastAPI application that exposes every backend
subsystem built so far (scraping/matching/profile/document-generation/
workflow) through a server-rendered Jinja2 + HTMX + Bootstrap interface,
plus a JSON REST API. Contains no business logic of its own — see
`app.py`'s module docstring. No authentication, no deployment configuration,
no automatic application submission (see docs/DASHBOARD.md).
"""
