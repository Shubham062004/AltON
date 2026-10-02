import time
import datetime
from typing import Optional, List

class ProactiveEngine:
    """
    Proactive 2.0 System Check-in Engine for ULTRON OS.
    Time-of-day and context-aware background check-ins.
    """

    def __init__(self, cooldown_seconds: int = 1200):
        self.cooldown_seconds = cooldown_seconds
        self.last_checkin_time = 0.0

    def can_checkin(self) -> bool:
        return (time.time() - self.last_checkin_time) >= self.cooldown_seconds

    def generate_proactive_prompt(self, active_projects: Optional[List[str]] = None) -> Optional[str]:
        if not self.can_checkin():
            return None

        self.last_checkin_time = time.time()
        hour = datetime.datetime.now().hour

        if 5 <= hour < 12:
            time_tone = "morning check-in"
        elif 12 <= hour < 18:
            time_tone = "afternoon review"
        else:
            time_tone = "evening recap"

        proj_str = f" Active project context: {', '.join(active_projects)}." if active_projects else ""
        
        return f"[PROACTIVE {time_tone.upper()}]:{proj_str} Ask the user if they require assistance or wish to review system tasks."
