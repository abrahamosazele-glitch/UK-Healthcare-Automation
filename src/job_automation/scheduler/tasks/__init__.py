"""
The 5 concrete scheduled task functions this milestone requires. Each is a
plain `(session) -> dict` function — see `scheduler_models.TaskFunc` — and
is wrapped (locking, retry, history recording) by `SchedulerService`, never
called directly outside of tests. Registered in `task_registry.py`.
"""
