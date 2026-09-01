"""ADK session-service decorator that emits append-only lifecycle evidence."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from google.adk.sessions.base_session_service import BaseSessionService

SessionSink = Callable[[str, str, dict[str, Any]], object]


class ObservedSessionService(BaseSessionService):
    def __init__(self, delegate: BaseSessionService, sink: SessionSink) -> None:
        self.delegate = delegate
        self.sink = sink

    async def create_session(self, **kwargs: Any) -> Any:
        session = await self.delegate.create_session(**kwargs)
        self.sink(
            session.id,
            "session.created",
            {"app_name": session.app_name, "user_id": session.user_id},
        )
        return session

    async def get_session(self, **kwargs: Any) -> Any:
        return await self.delegate.get_session(**kwargs)

    async def list_sessions(self, **kwargs: Any) -> Any:
        return await self.delegate.list_sessions(**kwargs)

    async def delete_session(self, **kwargs: Any) -> None:
        session_id = str(kwargs["session_id"])
        await self.delegate.delete_session(**kwargs)
        self.sink(session_id, "session.deleted", {"app_name": kwargs["app_name"]})

    async def append_event(self, session: Any, event: Any) -> Any:
        stored = await self.delegate.append_event(session, event)
        if not stored.partial:
            self.sink(
                session.id,
                "session.event",
                {
                    "event": stored.model_dump(mode="json"),
                    "state_keys": sorted(session.state),
                },
            )
        return stored

    async def get_user_state(self, **kwargs: Any) -> dict[str, Any]:
        return await self.delegate.get_user_state(**kwargs)

    async def flush(self) -> None:
        await self.delegate.flush()
