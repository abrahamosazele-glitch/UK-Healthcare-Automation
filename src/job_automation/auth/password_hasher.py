"""
Password hashing — bcrypt via passlib, the standard, well-reviewed choice
for this rather than hand-rolling anything with `hashlib`. `PasswordHasher`
is a thin wrapper (not a bare module-level `CryptContext`) purely so
`AuthService` can take one as a constructor-injected dependency, consistent
with every other service in this project (`MatchingEngine`, `DocumentService`,
`WorkflowService`, ...).
"""

from __future__ import annotations

from passlib.context import CryptContext

_CONTEXT = CryptContext(schemes=["bcrypt"], deprecated="auto")


class PasswordHasher:
    def hash(self, plain_password: str) -> str:
        return _CONTEXT.hash(plain_password)

    def verify(self, plain_password: str, hashed_password: str) -> bool:
        return _CONTEXT.verify(plain_password, hashed_password)
