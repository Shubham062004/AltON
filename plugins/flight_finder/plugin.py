from typing import Dict, Any, Callable
from plugins.base_plugin import BasePlugin

class FlightFinderPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "flight_finder"

    @property
    def description(self) -> str:
        return "Flight Finder Plugin: Flight availability & ticket pricing lookup."

    @property
    def permissions(self) -> str:
        return "SAFE"

    def search_flights(self, origin: str, destination: str, date: str = "tomorrow") -> Dict[str, Any]:
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                query = f"flights from {origin} to {destination} {date} price"
                res = list(ddgs.text(query, max_results=3))
            return {
                "status": "success",
                "origin": origin,
                "destination": destination,
                "date": date,
                "flight_options": res
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_tools(self) -> Dict[str, Callable]:
        return {
            "SearchFlights": self.search_flights
        }

def get_plugin_instance() -> BasePlugin:
    return FlightFinderPlugin()
