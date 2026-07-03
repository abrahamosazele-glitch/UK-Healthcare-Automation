"""
Thin wrapper around the Anthropic Claude API.

Will centralize: client instantiation using ANTHROPIC_API_KEY from
config.settings, model selection, retry/error handling, and a single
`generate(prompt: str, system: str | None = None) -> str` used by both
cv_generator.py and cover_letter_generator.py — so there is exactly one place
that talks to the AI provider, making it easy to swap models or add caching.
"""
