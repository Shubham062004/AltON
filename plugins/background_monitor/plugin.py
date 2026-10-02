import time
from typing import Dict, Any, Callable, List
from plugins.base_plugin import BasePlugin

class BackgroundMonitorPlugin(BasePlugin):
    """
    Background Topic Monitor Plugin.
    Watches user-specified topics (e.g. AI research, tech news) once a day.
    """
    def __init__(self):
        self.monitored_topics: List[str] = ["AI developments", "Python releases"]
        self.seen_headlines = set()

    @property
    def name(self) -> str:
        return "background_monitor"

    @property
    def description(self) -> str:
        return "Background Topic Watcher: Daily news monitoring for user-requested topics."

    @property
    def permissions(self) -> str:
        return "SAFE"

    def add_topic(self, topic: str) -> Dict[str, Any]:
        if topic and topic not in self.monitored_topics:
            self.monitored_topics.append(topic)
            return {"status": "success", "message": f"Now monitoring topic: '{topic}'", "topics": self.monitored_topics}
        return {"status": "info", "message": "Topic already monitored."}

    def check_updates(self) -> Dict[str, Any]:
        updates = []
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                for topic in self.monitored_topics:
                    res = list(ddgs.text(topic, max_results=2))
                    for item in res:
                        title = item.get("title", "")
                        if title and title not in self.seen_headlines:
                            self.seen_headlines.add(title)
                            updates.append({"topic": topic, "title": title, "link": item.get("href")})
        except Exception as e:
            return {"status": "error", "message": str(e)}

        return {"status": "success", "new_updates_count": len(updates), "updates": updates}

    def get_tools(self) -> Dict[str, Callable]:
        return {
            "AddTopic": self.add_topic,
            "CheckUpdates": self.check_updates
        }

def get_plugin_instance() -> BasePlugin:
    return BackgroundMonitorPlugin()
