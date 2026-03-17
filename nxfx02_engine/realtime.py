"""
RealTimeSignalEngine and RealTimeEvent model.
Manages async event streams from launchpad monitors and dispatches normalized events.
Safety: never places trades or alters positions.
"""
import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, List
from datetime import datetime

@dataclass
class RealTimeEvent:
    """Normalized event from launchpad monitors."""
    launchpad: str          # "Bankr" | "Virtuals" | "ApeStore"
    event_type: str         # "NEW_LAUNCH", "LP_ADD", etc.
    token_address: str
    timestamp: datetime
    payload: Dict[str, Any]

class RealTimeSignalEngine:
    """
    Fans in events from launchpad monitors and dispatches to subscribers.
    Guardrails:
    - Never place trades or alter positions.
    - Only emit events and feature data for other components.
    - Treat missing/incomplete data as reason to skip/abort, not guess.
    """
    def __init__(self, monitors: Dict[str, 'BaseLaunchpadMonitor']):
        self.monitors = monitors
        self.subscribers: List[Callable[[RealTimeEvent], None]] = []

    def subscribe(self, callback: Callable[[RealTimeEvent], None]) -> None:
        self.subscribers.append(callback)

    def _dispatch(self, event: RealTimeEvent) -> None:
        for cb in self.subscribers:
            cb(event)

    async def run(self) -> None:
        tasks = [monitor.run() for monitor in self.monitors.values()]
        await asyncio.gather(*tasks)
