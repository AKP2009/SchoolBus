from pydantic import BaseModel


class HandoverResponse(BaseModel):
    summary: str
