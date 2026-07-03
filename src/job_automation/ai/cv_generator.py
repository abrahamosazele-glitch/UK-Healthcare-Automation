"""
Generates a tailored CV for a specific job using the candidate's base profile.

Will implement `generate_cv(candidate: CandidateProfile, job: Job) -> Path`:
build a prompt via ai.prompts, call ai_client.generate(), render the AI's
structured output into templates/cv_template.jinja (or fill a .docx via
python-docx), and save the result under data/cvs/<job_id>.docx.
"""
