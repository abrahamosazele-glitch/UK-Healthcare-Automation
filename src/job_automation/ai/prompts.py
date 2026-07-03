"""
Prompt templates for CV and cover letter generation.

Will hold system/user prompt strings (or Jinja2-rendered prompt builders) that
combine the CandidateProfile with a specific Job's description, instructing
Claude to emphasize relevant experience, mirror the job's required
skills/keywords (for ATS matching), and produce UK healthcare-sector
appropriate tone and terminology (e.g. CQC, NMC, DBS check references).
"""
