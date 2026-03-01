# app.py
import streamlit as st
import json
import os
import uuid
from datetime import datetime, timedelta

import scheduler
from nl_parser import parse_command
from agents.registry import SKILL_REGISTRY, agent_registry
from agents.base import AgentStatus

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Task Orchestrator",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

TASKS_FILE = "tasks.json"

# ── Auto-start daemon on first load ───────────────────────────────────────────
if "daemon_started" not in st.session_state:
    scheduler.start_daemon()
    st.session_state.daemon_started = True

# ── Task I/O ───────────────────────────────────────────────────────────────────
def load_tasks():
    if os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, "r") as f:
            return json.load(f)
    return []

def save_tasks(tasks):
    with open(TASKS_FILE, "w") as f:
        json.dump(tasks, f, indent=2, default=str)

def add_task(task):
    tasks = load_tasks()
    tasks.append(task)
    save_tasks(tasks)

def update_task(tid, updates):
    tasks = load_tasks()
    for t in tasks:
        if t["id"] == tid:
            t.update(updates)
    save_tasks(tasks)

def delete_task(tid):
    tasks = load_tasks()
    save_tasks([t for t in tasks if t["id"] != tid])

# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.skill-card {
    background: #1e2130; border: 1px solid #2d3249; border-radius: 10px;
    padding: 10px 12px; margin-bottom: 7px; transition: border-color .2s;
}
.skill-card:hover { border-color: #4f6ef7; }
.skill-cat  { font-size:10px; color:#7c85a8; text-transform:uppercase; letter-spacing:1px; }
.skill-name { font-size:13px; font-weight:600; color:#e2e8f0; }
.skill-desc { font-size:11px; color:#94a3b8; margin-top:2px; }
.skill-agent{ font-size:10px; color:#4f6ef7; margin-top:2px; }
.nl-hint    { font-size:10px; color:#475569; margin-top:3px; }

.daemon-on  { background:#14532d; color:#86efac; padding:4px 12px; border-radius:99px; font-size:12px; font-weight:600; }
.daemon-off { background:#450a0a; color:#fca5a5; padding:4px 12px; border-radius:99px; font-size:12px; font-weight:600; }

.badge { display:inline-block; padding:2px 10px; border-radius:99px; font-size:11px; font-weight:600; }
.badge-immediate { background:#1e3a5f; color:#60a5fa; }
.badge-scheduled { background:#2d1f5e; color:#a78bfa; }
.badge-recurring  { background:#1a3a2a; color:#4ade80; }
.badge-completed  { background:#14532d; color:#86efac; }
.badge-pending    { background:#1e1b4b; color:#a5b4fc; }
.badge-running    { background:#451a03; color:#fcd34d; }
.badge-failed     { background:#450a0a; color:#fca5a5; }

.result-box { background:#0d1117; border:1px solid #2d3249; border-radius:6px;
              padding:10px 14px; font-size:12px; color:#86efac; margin-top:6px; font-family:monospace; }
.agent-tag  { font-size:10px; color:#818cf8; font-weight:600; text-transform:uppercase; letter-spacing:.5px; }
.section-hdr{ font-size:11px; font-weight:700; color:#64748b; text-transform:uppercase;
              letter-spacing:1.5px; margin:16px 0 8px 0; }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    # ── Daemon status ──────────────────────────────────────────────────────────
    st.markdown("### ⚙️ Background Daemon")
    d_status = scheduler.daemon_status()
    running  = d_status["running"]

    if running:
        st.markdown("<span class='daemon-on'>● RUNNING</span>", unsafe_allow_html=True)
        st.caption(f"Sweeping every {d_status['tick_interval']}s · Log: `{d_status['log_file']}`")
        if st.button("⏹ Stop Daemon", use_container_width=True):
            scheduler.stop_daemon()
            st.rerun()
    else:
        st.markdown("<span class='daemon-off'>○ STOPPED</span>", unsafe_allow_html=True)
        if st.button("▶ Start Daemon", use_container_width=True, type="primary"):
            scheduler.start_daemon()
            st.rerun()

    # ── Agent registry ─────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🤖 Registered Agents")
    for agent_name, agent in agent_registry.all_agents().items():
        skill_count = len(agent.get_skills())
        st.markdown(f"**{agent.name}** · {skill_count} skill{'s' if skill_count != 1 else ''}")

    # ── Skill registry ─────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🗂️ Skill Registry")
    categories: dict = {}
    for sid, skill in SKILL_REGISTRY.items():
        categories.setdefault(skill["category"], []).append((sid, skill))

    for cat, skills in categories.items():
        st.markdown(f"<div class='section-hdr'>{cat}</div>", unsafe_allow_html=True)
        for sid, skill in skills:
            st.markdown(f"""
            <div class='skill-card'>
                <div class='skill-cat'>{skill['icon']} {cat}</div>
                <div class='skill-name'>{skill['name']}</div>
                <div class='skill-desc'>{skill['description']}</div>
                <div class='skill-agent'>Agent: {skill['agent']}</div>
                <div class='nl-hint'>e.g. "{skill['example']}"</div>
            </div>""", unsafe_allow_html=True)

    # ── Log viewer ─────────────────────────────────────────────────────────────
    st.markdown("---")
    if st.checkbox("📜 Show scheduler log (last 20 lines)"):
        if os.path.exists(scheduler.LOG_FILE):
            with open(scheduler.LOG_FILE) as f:
                lines = f.readlines()
            st.code("".join(lines[-20:]), language="text")
        else:
            st.caption("No log yet.")

# ── Main ───────────────────────────────────────────────────────────────────────
st.title("🤖 AI Task Orchestrator")
st.caption("Natural language → Agent-backed skill execution · Scheduled, recurring, or immediate · Background daemon auto-runs due tasks")

col_left, col_right = st.columns([3, 2])

# ── Command input ──────────────────────────────────────────────────────────────
with col_left:
    st.markdown("### 💬 Natural Language Command")

    EXAMPLES = [
        "Analyze sales.csv and show summary statistics now",
        "Send email to team@company.com with subject 'Weekly Report' every Monday",
        "Search for latest AI research papers every day at 9am",
        "Scrape https://news.ycombinator.com every hour",
        "Generate a bar chart from revenue.csv tomorrow at 8am",
        "Summarize quarterly_report.pdf now",
        "Convert data.csv to JSON format now",
        "Notify me 'System check complete' every day",
    ]
    selected = st.selectbox("📌 Try an example or type your own:", [""] + EXAMPLES)
    command  = st.text_area(
        "Command", value=selected, height=80,
        placeholder="e.g. 'Analyze sales.csv every Monday at 9am'",
        label_visibility="collapsed",
    )

    ca, cb, cc = st.columns([2, 2, 1])
    with ca:
        override = st.selectbox("Override schedule:", ["Auto-detect", "Run Now", "Scheduled", "Recurring"])
    with cb:
        sched_dt     = None
        rec_override = None
        if override == "Scheduled":
            sched_dt = st.date_input("Date:", value=datetime.now().date() + timedelta(days=1))
            sched_t  = st.time_input("Time:", value=datetime.now().time())
        elif override == "Recurring":
            rec_override = st.selectbox("Repeat:", ["hourly", "daily", "weekly", "monthly"])
    with cc:
        st.markdown("<br>", unsafe_allow_html=True)
        submit = st.button("⚡ Add Task", type="primary", use_container_width=True)

    if submit and command.strip():
        with st.spinner("🧠 Parsing command…"):
            parsed = parse_command(command.strip())

        # Apply manual overrides
        if override == "Run Now":
            parsed.update({"schedule_type": "immediate", "scheduled_at": None, "recurrence": None})
        elif override == "Scheduled" and sched_dt:
            dt_combined = datetime.combine(sched_dt, sched_t)
            parsed.update({"schedule_type": "scheduled", "scheduled_at": dt_combined.isoformat(), "recurrence": None})
        elif override == "Recurring":
            parsed.update({"schedule_type": "recurring", "recurrence": rec_override, "scheduled_at": None})

        skill_meta = SKILL_REGISTRY.get(parsed.get("skill_id") or "", {})
        task = {
            "id":               str(uuid.uuid4())[:8],
            "command":          command.strip(),
            "skill_id":         parsed.get("skill_id"),
            "agent":            skill_meta.get("agent", "unknown"),
            "schedule_type":    parsed.get("schedule_type", "immediate"),
            "scheduled_at":     parsed.get("scheduled_at"),
            "recurrence":       parsed.get("recurrence"),
            "params":           parsed.get("params", {}),
            "task_description": parsed.get("task_description", command[:80]),
            "parsed_by":        parsed.get("parsed_by", "unknown"),
            "status":           "pending",
            "created_at":       datetime.now().isoformat(),
            "last_run":         None,
            "run_count":        0,
            "result":           None,
        }
        add_task(task)

        st.success(
            f"✅ Queued! &nbsp; Skill: **{skill_meta.get('name', '?')}** &nbsp;|&nbsp; "
            f"Agent: **{task['agent']}** &nbsp;|&nbsp; "
            f"Schedule: **{task['schedule_type']}** &nbsp;|&nbsp; "
            f"Parsed by: *{task['parsed_by']}*"
        )
        if not task["skill_id"]:
            st.warning("⚠️ Skill not detected — task saved without skill binding.")

        # Immediate tasks: daemon will pick up within TICK_INTERVAL seconds
        if task["schedule_type"] == "immediate":
            st.info(f"⏱ The background daemon will execute this task within {scheduler.TICK_INTERVAL}s automatically.")

        st.rerun()

    # Manual controls
    st.markdown("---")
    rc1, rc2 = st.columns(2)
    with rc1:
        if st.button("▶️ Execute Due Tasks Now", use_container_width=True):
            from scheduler import _load, _is_due, _run_task
            tasks = _load()
            due   = [t for t in tasks if _is_due(t)]
            if due:
                for t in due:
                    with st.spinner(f"Running {t['id']}…"):
                        _run_task(t)
                st.success(f"Ran {len(due)} task(s).")
            else:
                st.info("No tasks currently due.")
            st.rerun()
    with rc2:
        if st.button("🔄 Refresh Dashboard", use_container_width=True):
            st.rerun()

# ── Stats panel ────────────────────────────────────────────────────────────────
with col_right:
    st.markdown("### 📊 Task Stats")
    tasks     = load_tasks()
    total     = len(tasks)
    pending   = sum(1 for t in tasks if t["status"] == "pending")
    running   = sum(1 for t in tasks if t["status"] == "running")
    completed = sum(1 for t in tasks if t["status"] == "completed")
    failed    = sum(1 for t in tasks if t["status"] == "failed")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total",     total)
    m2.metric("Pending",   pending)
    m3.metric("Running",   running)
    m4.metric("Done",      completed)
    m5.metric("Failed",    failed)

    st.markdown("### 🗓️ By Schedule")
    for label, key, badge in [
        ("⚡ Immediate", "immediate", "badge-immediate"),
        ("📅 Scheduled", "scheduled", "badge-scheduled"),
        ("🔁 Recurring",  "recurring", "badge-recurring"),
    ]:
        ct = sum(1 for t in tasks if t["schedule_type"] == key)
        st.markdown(f"<span class='badge {badge}'>{label}: {ct}</span>&nbsp;", unsafe_allow_html=True)

    st.markdown("### 🤖 By Agent")
    agent_counts: dict = {}
    for t in tasks:
        a = t.get("agent", "unknown")
        agent_counts[a] = agent_counts.get(a, 0) + 1
    for agent_name, count in sorted(agent_counts.items(), key=lambda x: -x[1]):
        st.markdown(f"<span class='agent-tag'>{agent_name}</span> &nbsp;{count} task(s)", unsafe_allow_html=True)

    # Auto-refresh toggle
    st.markdown("---")
    auto_refresh = st.toggle("🔄 Auto-refresh every 10s", value=False)
    if auto_refresh:
        import time
        time.sleep(10)
        st.rerun()

# ── Dashboard ──────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("### 📋 Task Dashboard")

tasks = load_tasks()
if not tasks:
    st.info("No tasks yet. Type a command above to get started!")
else:
    f1, f2, f3 = st.columns(3)
    with f1:
        sf = st.multiselect("Status:", ["pending","running","completed","failed"],
                            default=["pending","running","completed","failed"])
    with f2:
        schf = st.multiselect("Schedule:", ["immediate","scheduled","recurring"],
                              default=["immediate","scheduled","recurring"])
    with f3:
        agf = st.multiselect("Agent:", list(agent_registry.all_agents().keys()))

    filtered = [
        t for t in reversed(tasks)
        if t["status"] in sf
        and t["schedule_type"] in schf
        and (not agf or t.get("agent") in agf)
    ]
    st.caption(f"Showing {len(filtered)} of {len(tasks)} tasks")

    for task in filtered:
        skill    = SKILL_REGISTRY.get(task.get("skill_id") or "", {})
        s_icon   = skill.get("icon", "❓")
        s_name   = skill.get("name", "Unknown Skill")
        status   = task["status"]
        stype    = task["schedule_type"]

        with st.expander(
            f"{s_icon}  [{task['id']}]  {task['task_description'][:65]}   ·  {status.upper()}",
            expanded=False
        ):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown(f"**Command:** `{task['command']}`")
                st.markdown(
                    f"**Skill:** {s_name} &nbsp;|&nbsp; "
                    f"**Agent:** <span class='agent-tag'>{task.get('agent','?')}</span> &nbsp;|&nbsp; "
                    f"<span class='badge badge-{stype}'>{stype}</span> &nbsp;"
                    f"<span class='badge badge-{status}'>{status}</span>",
                    unsafe_allow_html=True
                )
                if task.get("scheduled_at"):
                    st.markdown(f"**Scheduled at:** `{task['scheduled_at']}`")
                if task.get("recurrence"):
                    st.markdown(f"**Recurrence:** `{task['recurrence']}`")
                if task.get("params"):
                    st.markdown(f"**Params:** `{json.dumps(task['params'])}`")
                st.markdown(f"**Parsed by:** *{task.get('parsed_by','N/A')}* &nbsp;|&nbsp; **Runs:** {task.get('run_count',0)}")

                # Result rendering
                result = task.get("result")
                if result:
                    if isinstance(result, dict):
                        r_status = result.get("status", "")
                        output   = result.get("output", {})
                        icon     = "✅" if r_status == AgentStatus.SUCCESS.value else "❌"
                        st.markdown(f"<div class='result-box'>{icon} <b>{r_status.upper()}</b><br>{json.dumps(output, indent=2)}</div>",
                                    unsafe_allow_html=True)
                        if result.get("error"):
                            st.error(f"Error: {result['error']}")
                    else:
                        st.markdown(f"<div class='result-box'>{result}</div>", unsafe_allow_html=True)

                if task.get("last_run"):
                    st.caption(f"Last run: {task['last_run']}")

            with c2:
                if st.button("▶ Run Now", key=f"run_{task['id']}"):
                    from scheduler import _run_task
                    with st.spinner("Running…"):
                        _run_task(task)
                    st.rerun()

                if st.button("🗑 Delete", key=f"del_{task['id']}"):
                    delete_task(task["id"])
                    st.rerun()

                if status in ("completed", "failed"):
                    if st.button("↩ Reset", key=f"reset_{task['id']}"):
                        update_task(task["id"], {"status": "pending", "result": None, "run_count": 0})
                        st.rerun()