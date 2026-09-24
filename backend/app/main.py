import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.errors import Utf8JSONResponse, register_error_handlers
from app.jobs.scheduler import build_scheduler
from app.llm import config_error as llm_config_error
from app.replay.runtime import get_engine
from app.routers import (
    analytics,
    chat,
    events,
    handover,
    health,
    incidents,
    machine,
    operator,
    plan,
    predict,
    replay,
    scenario,
    stream,
    training,
    voice,
)

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if (reason := llm_config_error()) is not None:
        # the rest of the API works; /chat, /handover, /incidents/transcribe return 503
        log.warning("LLM features disabled: %s", reason)
    scheduler = build_scheduler() if get_settings().scheduler_enabled else None
    if scheduler is not None:
        scheduler.start()
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)
    # stop the replay and flush pending alert / health writes
    await get_engine().stop()


app = FastAPI(
    title="Smart Operator Assistant API",
    version="0.1.0",
    lifespan=lifespan,
    default_response_class=Utf8JSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_error_handlers(app)

for router in (
    health.router,
    predict.router,
    plan.router,
    machine.router,
    events.router,
    operator.router,
    chat.router,
    voice.router,
    handover.router,
    incidents.router,
    analytics.router,
    training.router,
    replay.router,
    scenario.router,
    stream.router,
):
    app.include_router(router)
