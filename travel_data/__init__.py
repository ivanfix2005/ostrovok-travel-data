"""Four synchronous tools and the asynchronous client for backend integration."""

from .client import TravelDataClient, call, get_route, get_weather, search_hotels, search_transport
from .config import Settings

__all__ = [
    "Settings",
    "TravelDataClient",
    "call",
    "search_hotels",
    "search_transport",
    "get_weather",
    "get_route",
]
__version__ = "1.0.0"
