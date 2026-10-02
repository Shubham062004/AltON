import httpx
from typing import Dict, Any, Callable
from plugins.base_plugin import BasePlugin

class WeatherPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "weather"

    @property
    def description(self) -> str:
        return "Weather Report Plugin: Live weather data & forecasts for any city."

    @property
    def permissions(self) -> str:
        return "SAFE"

    def get_weather(self, city: str = "New York") -> Dict[str, Any]:
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                res = list(ddgs.text(f"weather in {city} forecast", max_results=2))
            if res:
                return {"status": "success", "city": city, "weather_info": res[0].get("body")}
            return {"status": "info", "city": city, "weather_info": f"Weather data for {city} unavailable."}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_tools(self) -> Dict[str, Callable]:
        return {
            "GetWeather": self.get_weather
        }

def get_plugin_instance() -> BasePlugin:
    return WeatherPlugin()
