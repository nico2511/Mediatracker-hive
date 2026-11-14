"""RFC 7807 problem responses."""

from fastapi.responses import JSONResponse
from starlette.requests import Request


class Problem(Exception):
    def __init__(
        self,
        status: int,
        title: str,
        detail: str,
        *,
        headers: dict[str, str] | None = None,
        extra: dict | None = None,
        type_: str = "about:blank",
    ) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        self.headers = headers or {}
        self.extra = extra or {}
        self.type = type_
        super().__init__(detail)


def problem_response(
    request: Request,
    status: int,
    title: str,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
    extra: dict | None = None,
    type_: str = "about:blank",
) -> JSONResponse:
    body: dict = {
        "type": type_,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
    }
    if extra:
        body.update(extra)
    return JSONResponse(
        body,
        status_code=status,
        headers=headers,
        media_type="application/problem+json",
    )


def public_validation_errors(errors: list) -> list[dict]:
    cleaned = []
    for error in errors:
        cleaned.append(
            {
                "loc": list(error.get("loc", [])),
                "msg": error.get("msg", ""),
                "type": error.get("type", ""),
            }
        )
    return cleaned
