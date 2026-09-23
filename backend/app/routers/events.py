from typing import Annotated

from fastapi import APIRouter, Body, Depends

from app.core.auth import UserOrService, forbidden
from app.runtime import get_event_service
from app.schemas.events import AlertEvent, Event, EventResponse, FatigueSample
from app.services.events import EventService

router = APIRouter(tags=["events"])


@router.post("/events", response_model=EventResponse)
async def post_event(
    body: Annotated[Event, Body()],
    user: UserOrService,
    service: Annotated[EventService, Depends(get_event_service)],
) -> EventResponse:
    """Vision service (service token) and the operator app / voice (Supabase JWT).

    Alert types -> `safety` WebSocket message, `alerts` row (coalesced per episode),
    `safety_events` row, `alert` WebSocket message. fatigue_sample -> `fatigue_log` row and a
    `fatigue` WebSocket message.
    """
    if not user.can_act_for(body.operator_id):
        raise forbidden("Operators can only post events for themselves.")
    if isinstance(body, FatigueSample):
        event_id = await service.handle_fatigue(body)
        return EventResponse(stored=True, event_id=event_id)
    assert isinstance(body, AlertEvent)
    alert_id, event_id = await service.handle_alert(body)
    return EventResponse(stored=True, alert_id=alert_id, event_id=event_id)
