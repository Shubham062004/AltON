import os
import json
import time

class BriefingManager:
    """
    Session Memory & Morning Briefing Engine for ULTRON OS.
    Inspired by Mark-L (50): Summarizes conversations at session end and
    delivers a context-aware morning greeting recapping active projects.
    """

    def __init__(self, data_file: str = None):
        if not data_file:
            data_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "session_memory.json")
        self.data_file = data_file
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(self.data_file):
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump({"last_session_summary": "", "last_briefing_time": 0}, f, indent=2)

    def save_session_summary(self, summary: str):
        if not summary or not summary.strip():
            return
        data = {"last_session_summary": summary.strip(), "saved_at": time.time()}
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[BRIEFING MANAGER]: Session summary saved: '{summary[:60]}...'")

    def get_morning_briefing(self) -> str:
        if not os.path.exists(self.data_file):
            return "Good day, sir. All core systems are nominal."

        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            summary = data.get("last_session_summary", "").strip()
            current_time_str = time.strftime("%H:%M")

            if summary:
                briefing = f"Good morning, sir — it's {current_time_str}. Yesterday you were working on: {summary}. All systems are active."
                # Clear summary after consumed so it never repeats twice
                data["last_session_summary"] = ""
                data["last_briefing_time"] = time.time()
                with open(self.data_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                return briefing
            else:
                return f"Good morning, sir — it's {current_time_str}. ULTRON OS is ready for your instructions."
        except Exception as e:
            print(f"[BRIEFING MANAGER]: Error generating briefing: {e}")
            return "Good day, sir. Systems operational."
