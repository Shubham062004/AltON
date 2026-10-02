import enum
from typing import Dict, Any, Tuple

class SecurityLevel(enum.Enum):
    SAFE = "SAFE"           # Read-only queries, web search, memory lookups, TTS, OCR
    MEDIUM = "MEDIUM"       # Browser navigation, DOM clicking, reading web pages
    HIGH = "HIGH"           # Desktop window focus, app launching, desktop typing/mouse clicks
    CRITICAL = "CRITICAL"   # File deletion, terminal shell execution, email sending, system modification

class ActionSecurityGatekeeper:
    """
    Security & Permission Level Gatekeeper for ULTRON OS.
    Enforces authorization rules before executing automated actions.
    """

    @staticmethod
    def classify_action(step: Dict[str, Any]) -> SecurityLevel:
        stype = step.get("type", "").lower().strip()
        
        # CRITICAL actions
        critical_types = [
            "run_terminal", "run_python", "delete_file", "shutdown",
            "system_command", "send_email", "money_transfer"
        ]
        if stype in critical_types or "delete" in stype or "terminal" in stype:
            return SecurityLevel.CRITICAL

        # HIGH actions
        high_types = [
            "launch_app", "close_app", "focus_window", "open_app_and_type",
            "press_key", "hotkey", "type", "open_file", "save_file"
        ]
        if stype in high_types or "app" in stype:
            return SecurityLevel.HIGH

        # MEDIUM actions
        medium_types = [
            "navigate", "new_tab", "close_tab", "switch_tab",
            "click", "double_click", "hover", "clear_input",
            "wait_for_selector", "smart_task", "smart_open_url",
            "vision_click_element"
        ]
        if stype in medium_types:
            return SecurityLevel.MEDIUM

        # SAFE actions default
        return SecurityLevel.SAFE

    @staticmethod
    def authorize_step(step: Dict[str, Any], auto_approve_high: bool = True) -> Tuple[bool, SecurityLevel, str]:
        """
        Evaluates authorization for a plan step.
        Returns: (is_allowed, security_level, reason_message)
        """
        level = ActionSecurityGatekeeper.classify_action(step)
        stype = step.get("type", "unknown")

        if level == SecurityLevel.SAFE:
            return True, level, f"[SECURITY SAFE]: Authorized step '{stype}'"

        if level == SecurityLevel.MEDIUM:
            return True, level, f"[SECURITY MEDIUM]: Authorized browser action '{stype}'"

        if level == SecurityLevel.HIGH:
            if auto_approve_high:
                return True, level, f"[SECURITY HIGH]: Desktop automation step '{stype}' pre-approved."
            return False, level, f"[SECURITY REQUIRE_APPROVAL]: Desktop action '{stype}' requires user confirmation."

        if level == SecurityLevel.CRITICAL:
            # Check if strict override flag present
            if step.get("user_confirmed", False):
                return True, level, f"[SECURITY CRITICAL]: Critical action '{stype}' confirmed by user."
            return False, level, f"[SECURITY CRITICAL BLOCKED]: Action '{stype}' is classified as CRITICAL and requires explicit user confirmation."

        return True, level, f"[SECURITY]: Action '{stype}' authorized."
