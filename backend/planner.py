import json
import re

def map_legacy_action(plan) -> list:
    """Helper to convert legacy single action format into multi-step format."""
    if isinstance(plan, list):
        return plan
    if not isinstance(plan, dict):
        return []
    if "steps" in plan:
        return plan["steps"]
    
    atype = plan.get("action_type") or plan.get("type")
    if not atype:
        return []

    # Guarantee 'type' key exists on the plan dict
    if "type" not in plan:
        plan["type"] = atype

    if atype in ("open_app_and_type", "launch_app_and_type"):
        app_name = plan.get("app") or plan.get("app_name") or plan.get("name") or "notepad"
        text = plan.get("text") or plan.get("text_to_type") or ""
        return [
            {"type": "launch_app", "app": app_name},
            {"type": "wait", "seconds": 2},
            {"type": "type", "text": text}
        ]
    elif atype in ("open_app", "launch_app", "start_app", "run_app"):
        app_name = plan.get("app") or plan.get("app_name") or plan.get("name") or plan.get("application") or ""
        return [{"type": "launch_app", "app": app_name}]
    elif atype in ("open_url", "smart_open_url", "smart_open", "open_browser", "open_url_and_interact"):
        url = plan.get("url") or "https://google.com"
        query = plan.get("search_query") or plan.get("query")
        return [{"type": "smart_open_url", "url": url, "search_query": query}]
    elif atype in ("open_maps_location", "open_maps"):
        location = plan.get("location_name") or plan.get("location") or plan.get("query") or ""
        return [{"type": "open_maps_location", "location_name": location}]
    elif atype == "send_email":
        to = plan.get("to", "")
        subject = plan.get("subject", "")
        body = plan.get("body", "")
        return [{"type": "new_tab", "url": f"mailto:{to}?subject={subject}&body={body}"}]
    elif atype == "system_command":
        cmd_map = {
            "open_notepad": "notepad",
            "open_calc": "calc",
            "open_calculator": "calc",
            "open_explorer": "explorer",
            "open_vscode": "vscode",
            "open_volume": "volume"
        }
        return [{"type": "launch_app", "app": cmd_map.get(plan.get("command"), "notepad")}]
    elif atype == "save_memory":
        return [{"type": "save_memory", "fact": plan.get("fact")}]

    return [plan]

def parse_execution_plan(llm_output: str) -> tuple:
    """
    Parses LLM response to extract the structured execution plan (JSON)
    and separate it from the conversational response text.
    
    Returns:
        (clean_conversational_text, list_of_steps)
    """
    # 1. Look for <<<ACTION>>> delimiters
    match = re.search(r'<<<ACTION>>>([\s\S]*?)<<<END_ACTION>>>', llm_output)
    if match:
        clean_text = llm_output.replace(match.group(0), '').strip()
        try:
            plan = json.loads(match.group(1).strip())
            return clean_text, map_legacy_action(plan)
        except Exception as e:
            print(f"Error parsing delimited action JSON: {e}")
            
    # 2. Fallback: Search for any json code block
    json_block_match = re.search(r'```json\s*([\s\S]*?)\s*```', llm_output)
    if json_block_match:
        clean_text = llm_output.replace(json_block_match.group(0), '').strip()
        try:
            plan = json.loads(json_block_match.group(1).strip())
            return clean_text, map_legacy_action(plan)
        except Exception as e:
            print(f"Error parsing json code block: {e}")

    # 3. Fallback: Try to find the first '{' and last '}'
    brace_start = llm_output.find('{')
    brace_end = llm_output.rfind('}')
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        json_candidate = llm_output[brace_start:brace_end + 1]
        try:
            plan = json.loads(json_candidate)
            clean_text = (llm_output[:brace_start] + llm_output[brace_end + 1:]).strip()
            return clean_text, map_legacy_action(plan)
        except Exception:
            pass

    return llm_output, []
