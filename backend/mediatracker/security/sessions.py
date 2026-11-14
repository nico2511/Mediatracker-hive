"""Cookie de session signé (HttpOnly, SameSite=Lax).

L'attribut Secure est géré dans ``mediatracker.api.cookies`` selon ``COOKIE_SECURE``.
"""

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_COOKIE = "mt_session"
CSRF_COOKIE = "mt_csrf"
CSRF_HEADER = "X-CSRF-Token"


class SessionSigner:
    def __init__(self, secret_key: str, max_age: int) -> None:
        self.max_age = max_age
        self._serializer = URLSafeTimedSerializer(secret_key, salt="mt-session")

    def dumps(self, user_id: int, session_epoch: int = 0) -> str:
        return self._serializer.dumps({"uid": user_id, "e": session_epoch})

    def loads(self, token: str) -> tuple[int, int] | None:
        try:
            data = self._serializer.loads(token, max_age=self.max_age)
        except (BadSignature, SignatureExpired):
            return None
        if not isinstance(data, dict):
            return None
        uid = data.get("uid")
        if not isinstance(uid, int):
            return None
        epoch = data.get("e", 0)
        if not isinstance(epoch, int):
            return None
        return uid, epoch
