"""HMAC-SHA256 for generic webhooks.

The signed string is ``{timestamp}.{raw body}``. The timestamp is unix seconds.
"""

import hashlib
import hmac

TIMESTAMP_HEADER = "X-MediaTracker-Timestamp"
SIGNATURE_HEADER = "X-MediaTracker-Signature"


def sign_body(secret: str, timestamp: str, body: bytes) -> str:
    message = timestamp.encode("utf-8") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def signature_headers(secret: str, timestamp: str, body: bytes) -> dict[str, str]:
    return {
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: sign_body(secret, timestamp, body),
    }
