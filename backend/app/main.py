"""Smart Operator Assistant FastAPI app (docs/architecture.md).

Endpoints implemented so far: /health, /predict/task-time,
/operators/{id}/efficiency. Replay, alerts, chat, handover etc. come next.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.errors import (
    ApiError,
    api_error_handler,
    internal_error_handler,
    validation_error_handler,
)
from app.routers import health, operators, predict


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.data import get_repo

    try:
        get_repo()
    except Exception as exc:  # noqa: BLE001 -- startup must not crash without a model
        import logging

        logging.getLogger(__name__).warning("repo warm-up failed: %s", exc)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Smart Operator Assistant", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(predict.router)
    app.include_router(operators.router)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, internal_error_handler)
    return app


app = create_app()
