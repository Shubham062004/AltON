import httpx
from typing import Dict, Any, Callable
from plugins.base_plugin import BasePlugin

class ResearchPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "research"

    @property
    def description(self) -> str:
        return "Research Agent: Real-time search, article summarization, and report generation."

    @property
    def permissions(self) -> str:
        return "SAFE"

    def search_topic(self, topic: str) -> Dict[str, Any]:
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(topic, max_results=5))
            return {"status": "success", "topic": topic, "results": results}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_tools(self) -> Dict[str, Callable]:
        return {
            "Search": self.search_topic
        }

def get_plugin_instance() -> BasePlugin:
    return ResearchPlugin()
