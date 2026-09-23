from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas.common import ErrorBody, ErrorResponse


class ApiError(Exception):
    """Raise to return the contract error shape: {"error": {"code", "message"}}."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def not_implemented(what: str) -> ApiError:
    return ApiError(501, "NOT_IMPLEMENTED", f"{what} is not implemented yet.")


def _body(code: str, message: str) -> dict[str, object]:
    return ErrorResponse(error=ErrorBody(code=code, message=message)).model_dump()


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content=_body("VALIDATION_ERROR", str(exc.errors())))
