"""Request-local credentials for one reserved, loopback review stage."""

from contextvars import ContextVar
from urllib.parse import urlsplit

CURRENT_REVIEW_ADMISSION = ContextVar("shine_review_admission", default=None)


def review_admission_headers(base_url):
    admission = CURRENT_REVIEW_ADMISSION.get()
    if admission is None:
        return {}
    expected, token, review_id = admission
    parsed = urlsplit(base_url)
    if (base_url != expected or parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("REVIEW_ADMISSION_ENDPOINT_MISMATCH")
    return {"X-Shine-Handoff-Token": token, "X-Shine-Review-Id": review_id}
