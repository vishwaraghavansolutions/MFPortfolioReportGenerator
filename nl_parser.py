# nl_parser.py
import re
import json
from datetime import datetime, timedelta
from typing import Optional

import anthropic
from agents.registry import SKILL_REGISTRY

RECURRENCE_PATTERNS = {
    r"\bevery\s+day\b|\bdaily\b":    "daily",
    r"\bevery\s+week\b|\bweekly\b":  "weekly",
    r"\bevery\s+hour\b|\bhourly\b":  "hourly",
    r"\bevery\s+monday\b":           "weekly_mon",
    r"\bevery\s+friday\b":           "weekly_fri",
    r"\bevery\s+month\b|\bmonthly\b":"monthly",
}

SKILL_KEYWORDS = {
    r"\bread\b|\bopen\b|\bload\b":          "read_file",
    r"\bwrite\b|\bsave\b|\bcreate file\b":  "write_file",
    r"\bconvert\b|\btransform\b":           "convert_file",
    r"\bsearch\b|\blook up\b|\bfind\b":     "web_search",
    r"\bscrape\b|\bcrawl\b|\bfetch url\b":  "scrape_url",
    r"\bemail\b|\bsend mail\b|\bmail\b":    "send_email",
    r"\bnotif\b|\balert\b|\bping\b":        "send_notification",
    r"\banalyz\b|\bstats\b|\bstatistic\b":  "analyze_data",
    r"\bchart\b|\bgraph\b|\bplot\b|\bvisuali\b": "generate_chart",
    r"\bsummari\b|\breport\b|\bbrief\b":    "summarize_report",
}


def regex_parse(command: str) -> dict:
    cmd = command.lower()

    skill_id = next(
        (sid for pat, sid in SKILL_KEYWORDS.items() if re.search(pat, cmd)),
        None,
    )
    recurrence = next(
        (rec for pat, rec in RECURRENCE_PATTERNS.items() if re.search(pat, cmd)),
        None,
    )

    schedule_type = "immediate"
    scheduled_at  = None

    if recurrence:
        schedule_type = "recurring"
    elif re.search(r"\bnow\b|\bimmediately\b|\bright away\b", cmd):
        schedule_type = "immediate"
    else:
        dt_match = re.search(
            r"(tomorrow|today|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{4}-\d{2}-\d{2})"
            r"(?:\s+at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?))?",
            cmd,
        )
        if dt_match:
            schedule_type = "scheduled"
            word     = dt_match.group(1)
            time_str = dt_match.group(2)
            base     = datetime.now()
            if word == "tomorrow":
                base = base + timedelta(days=1)
            if time_str:
                for fmt in ("%I:%M%p", "%I%p", "%H:%M"):
                    try:
                        t = datetime.strptime(time_str.strip().upper(), fmt.upper())
                        base = base.replace(hour=t.hour, minute=t.minute, second=0)
                        break
                    except ValueError:
                        pass
            scheduled_at = base.isoformat()

    return {
        "skill_id":      skill_id,
        "schedule_type": schedule_type,
        "recurrence":    recurrence,
        "scheduled_at":  scheduled_at,
        "params":        {},
        "task_description": command[:100],
        "parsed_by":     "regex",
    }


def claude_parse(command: str) -> Optional[dict]:
    try:
        client = anthropic.Anthropic()
        skills_meta = {
            k: {"description": v["description"], "params": v["params"], "agent": v["agent"]}
            for k, v in SKILL_REGISTRY.items()
        }
        prompt = f"""You are a task orchestration assistant. Parse this natural language command into a structured task.

Available skills (skill_id → metadata):
{json.dumps(skills_meta, indent=2)}

Command: "{command}"

Respond ONLY with valid JSON — no markdown fences, no explanation:
{{
  "skill_id": "<skill_id or null>",
  "schedule_type": "<immediate|scheduled|recurring>",
  "scheduled_at": "<ISO 8601 datetime string or null>",
  "recurrence": "<daily|weekly|monthly|hourly|weekly_mon|weekly_fri|null>",
  "params": {{<extracted param key-values relevant to the skill>}},
  "task_description": "<short human-readable summary>"
}}

Rules:
- Use schedule_type=immediate when the user says "now", "immediately", or gives no time.
- Use schedule_type=scheduled for a specific future date/time.
- Use schedule_type=recurring for repeating patterns ("every day", "hourly", "weekly", etc.).
- Extract as many params as you can from the command text.
- Today is {datetime.now().strftime("%Y-%m-%d %H:%M")}."""

        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        result = json.loads(text)
        result["parsed_by"] = "Claude API"
        return result
    except Exception:
        return None


def parse_command(command: str) -> dict:
    """Try Claude first, fall back to regex."""
    result = claude_parse(command)
    if result and result.get("skill_id"):
        return result
    fallback = regex_parse(command)
    return fallback