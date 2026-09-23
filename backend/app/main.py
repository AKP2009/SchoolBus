from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.replay.runtime import get_engine  # noqa: E402
from app.routers import (
    analytics,
    chat,
    events,
    handover,
    health,
    incidents,
    machine,
    plan,
    predict,
    replay,
    scenario,
    stream,
    voice,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    # stop the replay and flush pending alert / health writes
    await get_engine().stop()


app = FastAPI(title="Smart Operator Assistant API", version="0.1.0", lifespan=lifespan)

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
    chat.router,
    voice.router,
    handover.router,
    incidents.router,
    analytics.router,
    replay.router,
    scenario.router,
    stream.router,
):
    app.include_router(router)
