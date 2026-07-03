"""
Generates a tailored cover letter for a specific job.

Will implement `generate_cover_letter(candidate: CandidateProfile, job: Job)
-> Path`: builds a prompt via ai.prompts referencing the employer name, role
title, and key requirements pulled from the job description, calls
ai_client.generate(), renders into templates/cover_letter_template.jinja, and
saves under data/cover_letters/<job_id>.docx.
"""
