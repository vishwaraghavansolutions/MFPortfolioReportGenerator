# scheduler.py
"""
Background daemon that ticks every TICK_INTERVAL seconds,
checks tasks.json for due tasks and executes them via the agent registry.
Uses a file-lock so concurrent Streamlit reruns don't double-execute.
"""
import json
import os
import time
import threading
import logging
from datetime import datetime, timedelta
from typing import Optional

from agents.registry import agent_registry, AgentStatus

TASKS_FILE   = "tasks.json"
LOCK_FILE    = ".scheduler.lock"
TICK_INTERVAL = 10          # seconds between scheduler sweeps
LOG_FILE     = "scheduler.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [SCHEDULER] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

RECURRENCE_SECONDS = {
    "hourly":     3600,
    "daily":      86_400,
    "weekly":     604_800,
    "weekly_mon": 604_800,
    "weekly_fri": 604_800,
    "monthly":    2_592_000,
}


# ── JSON helpers (thread-safe via a module-level lock) ─────────────────────────
_file_lock = threading.Lock()

def _load() -> list:
    with _file_lock:
        if not os.path.exists(TASKS_FILE):
            return []
        with open(TASKS_FILE, "r") as f:
            return json.load(f)

def _save(tasks: list):
    with _file_lock:
        with open(TASKS_FILE, "w") as f:
            json.dump(tasks, f, indent=2, default=str)

def _update(task_id: str, updates: dict):
    tasks = _load()
    for t in tasks:
        if t["id"] == task_id:
            t.update(updates)
    _save(tasks)


# ── Due-task logic ─────────────────────────────────────────────────────────────
def _is_due(task: dict) -> bool:
    now  = datetime.now()
    stype = task.get("schedule_type", "immediate")

    if task["status"] in ("running", "completed", "failed"):
        # Recurring tasks reset to pending after each run, so they'll re-trigger
        if stype != "recurring" or task["status"] != "pending":
            return False

    if stype == "immediate":
        return task["status"] == "pending"

    if stype == "scheduled":
        at = task.get("scheduled_at")
        if not at:
            return False
        return now >= datetime.fromisoformat(at) and task["status"] == "pending"

    if stype == "recurring":
        if task["status"] != "pending":
            return False
        last = task.get("last_run")
        if not last:
            return True
        interval = RECURRENCE_SECONDS.get(task.get("recurrence", "daily"), 86_400)
        return (now - datetime.fromisoformat(last)).total_seconds() >= interval

    return False


def _run_task(task: dict):
    task_id  = task["id"]
    skill_id = task.get("skill_id")
    params   = task.get("params", {})
    now_iso  = datetime.now().isoformat()

    logging.info(f"Executing task {task_id} | skill={skill_id}")
    _update(task_id, {"status": "running", "started_at": now_iso})

    response = agent_registry.execute(skill_id or "", params)
    result_dict = response.to_dict()

    stype = task.get("schedule_type", "immediate")
    new_status = (
        "pending"   if stype == "recurring" and response.status == AgentStatus.SUCCESS
        else "completed" if response.status == AgentStatus.SUCCESS
        else "failed"
    )

    _update(task_id, {
        "status":       new_status,
        "result":       result_dict,
        "last_run":     now_iso,
        "completed_at": now_iso,
        "run_count":    task.get("run_count", 0) + 1,
    })
    logging.info(f"Task {task_id} finished → {new_status} | agent_status={response.status.value}")


# ── Daemon loop ────────────────────────────────────────────────────────────────
_daemon_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def _daemon_loop():
    logging.info("Scheduler daemon started")
    while not _stop_event.is_set():
        try:
            tasks = _load()
            due   = [t for t in tasks if _is_due(t)]
            if due:
                logging.info(f"Found {len(due)} due task(s)")
            for task in due:
                # Run each due task in its own thread so long tasks don't block
                t = threading.Thread(target=_run_task, args=(task,), daemon=True)
                t.start()
        except Exception as exc:
            logging.error(f"Scheduler sweep error: {exc}")
        _stop_event.wait(TICK_INTERVAL)
    logging.info("Scheduler daemon stopped")


def start_daemon():
    global _daemon_thread
    if _daemon_thread and _daemon_thread.is_alive():
        return  # already running
    _stop_event.clear()
    _daemon_thread = threading.Thread(target=_daemon_loop, name="SchedulerDaemon", daemon=True)
    _daemon_thread.start()


def stop_daemon():
    _stop_event.set()


def is_running() -> bool:
    return _daemon_thread is not None and _daemon_thread.is_alive()


def daemon_status() -> dict:
    return {
        "running":       is_running(),
        "tick_interval": TICK_INTERVAL,
        "log_file":      LOG_FILE,
    }