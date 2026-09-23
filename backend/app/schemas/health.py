from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    models: dict[str, str]  # model name -> version, or "not_loaded"
