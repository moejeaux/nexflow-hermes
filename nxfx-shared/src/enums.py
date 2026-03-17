"""Enums shared across all NXFX agents."""

from enum import Enum


class ActionMode(str, Enum):
    FAST = "FAST"
    WAIT = "WAIT"
    BLOCK = "BLOCK"


class PositionAction(str, Enum):
    HOLD = "HOLD"
    SOFT_DERISK = "SOFT_DERISK"
    HARD_EXIT = "HARD_EXIT"
    NO_ENTRY = "NO_ENTRY"


class MarketRegime(str, Enum):
    HOT = "HOT"
    NORMAL = "NORMAL"
    COLD = "COLD"


class EntryStyle(str, Enum):
    SINGLE = "single"
    SLICED = "sliced"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class TimeInForce(str, Enum):
    IOC = "ioc"
    GTC = "gtc"
