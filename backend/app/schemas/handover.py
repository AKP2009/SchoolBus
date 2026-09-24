from pydantic import BaseModel


class HandoverResponse(BaseModel):
    summary: str
    pre_generated: bool = False  # from backend/cache/demo.json because the LLM was unavailable
