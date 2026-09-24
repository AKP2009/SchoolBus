import logging
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.common import ErrorBody, ErrorResponse

log = logging.getLogger(__name__)

HTTP_CODES = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    501: "NOT_IMPLEMENTED",
    503: "UNAVAILABLE",
}


class Utf8JSONResponse(JSONResponse):
    """JSON with an explicit charset. Without it, Windows PowerShell 5.1 (Invoke-RestMethod)
    and some other clients decode the body as ISO-8859-1, so "—" in alert titles shows as "â€”".
    The app's default response class; the error handlers use it too."""

    media_type = "application/json; charset=utf-8"


class ApiError(Exception):
    """Raise to return the contract error shape: {"error": {"code", "message"}}."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def not_implemented(what: str) -> ApiError:
    return ApiError(501, "NOT_IMPLEMENTED", f"{what} is not implemented yet.")


def model_not_loaded(what: str, notebook: str, e: Exception) -> ApiError:
    log.error("%s model not loaded: %s", what, e)
    return ApiError(
        503, "MODEL_NOT_LOADED", f"{what} model is not loaded. Run {notebook} and restart."
    )


def _body(code: str, message: str) -> dict[str, object]:
    return ErrorResponse(error=ErrorBody(code=code, message=message)).model_dump()


def validation_message(errors: Sequence[Any]) -> str:
    """'body.distance_m: Input should be a valid number; …' (the first few problems)."""
    parts = []
    for e in list(errors)[:5]:
        loc = ".".join(str(p) for p in e.get("loc", ()) if p not in ("body",)) or "body"
        parts.append(f"{loc}: {e.get('msg', 'invalid')}")
    return "; ".join(parts) or "Invalid request."


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> Utf8JSONResponse:
        return Utf8JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> Utf8JSONResponse:
        return Utf8JSONResponse(
            status_code=400, content=_body("VALIDATION_ERROR", validation_message(exc.errors()))
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> Utf8JSONResponse:
        code = HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return Utf8JSONResponse(
            status_code=exc.status_code,
            content=_body(code, str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> Utf8JSONResponse:
        log.exception("unhandled error: %s", exc)
        return Utf8JSONResponse(
            status_code=500, content=_body("INTERNAL_ERROR", "Something went wrong on the server.")
        )
