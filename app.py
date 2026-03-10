import os
import sqlite3
import signal
import webbrowser
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, render_template, request, redirect, url_for, g, jsonify

app = Flask(__name__)

DB_NAME = "todo.db"
HOST = "127.0.0.1"
PORT = 5001

IST = ZoneInfo("Europe/Istanbul")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")


# ---------------- DB helpers ----------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_NAME)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            deadline TEXT,
            source TEXT DEFAULT 'manual',
            created_at_utc TEXT NOT NULL
        )
    """)

    # Add deadline column if missing (migration)
    try:
        db.execute("ALTER TABLE tasks ADD COLUMN deadline TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE tasks ADD COLUMN source TEXT DEFAULT 'manual'")
    except Exception:
        pass

    db.execute("""
        CREATE TABLE IF NOT EXISTS ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            important INTEGER NOT NULL DEFAULT 0,
            urgent INTEGER NOT NULL DEFAULT 0,
            done INTEGER NOT NULL DEFAULT 0,
            deadline TEXT,
            created_at_utc TEXT NOT NULL
        )
    """)

    try:
        db.execute("ALTER TABLE ideas ADD COLUMN deadline TEXT")
    except Exception:
        pass

    db.commit()


@app.before_request
def before_request():
    init_db()


def now_utc_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------- Time formatting ----------------
@app.template_filter("istanbul_time")
def istanbul_time(utc_iso: str) -> str:
    if not utc_iso:
        return ""
    try:
        dt_utc = datetime.fromisoformat(utc_iso)
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        dt_ist = dt_utc.astimezone(IST)
        return dt_ist.strftime("%d %b %Y, %H:%M")
    except Exception:
        return utc_iso


@app.template_filter("deadline_display")
def deadline_display(deadline_str: str) -> str:
    if not deadline_str:
        return ""
    try:
        dt = datetime.fromisoformat(deadline_str)
        return dt.strftime("%d %b %Y")
    except Exception:
        return deadline_str


@app.template_filter("deadline_urgency")
def deadline_urgency(deadline_str: str) -> str:
    """Returns: overdue | today | soon | upcoming | none"""
    if not deadline_str:
        return "none"
    try:
        dt = datetime.fromisoformat(deadline_str).date()
        today = datetime.now(IST).date()
        delta = (dt - today).days
        if delta < 0:
            return "overdue"
        elif delta == 0:
            return "today"
        elif delta <= 3:
            return "soon"
        else:
            return "upcoming"
    except Exception:
        return "none"


# ---------------- Routes: Tasks ----------------
@app.route("/", methods=["GET"])
def tasks_page():
    filter_value = request.args.get("filter", "all")
    sort_value = request.args.get("sort", "deadline")  # deadline | created
    db = get_db()

    where_clause = ""
    if filter_value == "active":
        where_clause = " WHERE t.done=0"
    elif filter_value == "completed":
        where_clause = " WHERE t.done=1"

    if sort_value == "deadline":
        order_clause = " ORDER BY CASE WHEN t.deadline IS NULL OR t.deadline='' THEN 1 ELSE 0 END, t.deadline ASC, t.id DESC"
    else:
        order_clause = " ORDER BY t.id DESC"

    base_query = (
        "SELECT t.*, "
        "CASE WHEN i.important=1 AND i.urgent=1 THEN 'both' "
        "WHEN i.important=1 THEN 'important' "
        "WHEN i.urgent=1 THEN 'urgent' "
        "ELSE NULL END as matrix_label "
        "FROM tasks t LEFT JOIN ideas i ON i.text=t.text"
        + where_clause + order_clause
    )

    tasks = db.execute(base_query).fetchall()
    total = db.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
    remaining = db.execute("SELECT COUNT(*) AS c FROM tasks WHERE done=0").fetchone()["c"]
    overdue = db.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE done=0 AND deadline IS NOT NULL AND deadline < ?",
        (datetime.now(IST).date().isoformat(),)
    ).fetchone()["c"]

    return render_template(
        "tasks.html",
        tasks=tasks,
        filter_value=filter_value,
        sort_value=sort_value,
        total=total,
        remaining=remaining,
        overdue=overdue,
    )


@app.route("/tasks/add", methods=["POST"])
def add_task():
    text = request.form.get("text", "").strip()
    deadline = request.form.get("deadline", "").strip() or None
    source = request.form.get("source", "manual")
    if text:
        db = get_db()
        db.execute(
            "INSERT INTO tasks (text, done, deadline, source, created_at_utc) VALUES (?, 0, ?, ?, ?)",
            (text, deadline, source, now_utc_iso()),
        )
        db.commit()
    return redirect(url_for("tasks_page", filter=request.args.get("filter", "all"), sort=request.args.get("sort", "deadline")))


@app.route("/tasks/toggle/<int:task_id>", methods=["POST"])
def toggle_task(task_id):
    db = get_db()
    row = db.execute("SELECT done FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row:
        db.execute("UPDATE tasks SET done=? WHERE id=?", (0 if row["done"] == 1 else 1, task_id))
        db.commit()
    return redirect(url_for("tasks_page", filter=request.args.get("filter", "all"), sort=request.args.get("sort", "deadline")))


@app.route("/tasks/delete/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    db = get_db()
    row = db.execute("SELECT text, deadline FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row:
        db.execute("DELETE FROM ideas WHERE text=?", (row["text"],))
    db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    db.commit()
    return redirect(url_for("tasks_page", filter=request.args.get("filter", "all"), sort=request.args.get("sort", "deadline")))


@app.route("/tasks/clear_completed", methods=["POST"])
def clear_completed_tasks():
    db = get_db()
    db.execute("DELETE FROM tasks WHERE done=1")
    db.commit()
    return redirect(url_for("tasks_page", filter=request.args.get("filter", "all")))


# ---------------- Routes: Ideas (Eisenhower) ----------------
@app.route("/ideas", methods=["GET"])
def ideas_page():
    db = get_db()
    ideas = db.execute("SELECT * FROM ideas ORDER BY CASE WHEN deadline IS NULL OR deadline='' THEN 1 ELSE 0 END, deadline ASC, id DESC").fetchall()

    quadrants = {"do_now": [], "schedule": [], "delegate": [], "eliminate": []}
    for idea in ideas:
        imp, urg = bool(idea["important"]), bool(idea["urgent"])
        if imp and urg:
            quadrants["do_now"].append(idea)
        elif imp and not urg:
            quadrants["schedule"].append(idea)
        elif not imp and urg:
            quadrants["delegate"].append(idea)
        else:
            quadrants["eliminate"].append(idea)

    total = db.execute("SELECT COUNT(*) AS c FROM ideas").fetchone()["c"]
    remaining = db.execute("SELECT COUNT(*) AS c FROM ideas WHERE done=0").fetchone()["c"]

    return render_template("ideas.html", quadrants=quadrants, total=total, remaining=remaining)


def label_to_flags(label):
    label = (label or "none").lower()
    if label == "both":
        return 1, 1
    if label == "important":
        return 1, 0
    if label == "urgent":
        return 0, 1
    return 0, 0


@app.route("/ideas/add", methods=["POST"])
def add_idea():
    text = request.form.get("text", "").strip()
    label = request.form.get("label", "none")
    deadline = request.form.get("deadline", "").strip() or None
    if text:
        important, urgent = label_to_flags(label)
        db = get_db()
        db.execute(
            "INSERT INTO ideas (text, important, urgent, done, deadline, created_at_utc) VALUES (?, ?, ?, 0, ?, ?)",
            (text, important, urgent, deadline, now_utc_iso()),
        )
        # Also add to tasks list
        db.execute(
            "INSERT INTO tasks (text, done, deadline, source, created_at_utc) VALUES (?, 0, ?, 'matrix', ?)",
            (text, deadline, now_utc_iso()),
        )
        db.commit()
    return redirect(url_for("ideas_page"))


@app.route("/ideas/toggle/<int:idea_id>", methods=["POST"])
def toggle_idea(idea_id):
    db = get_db()
    row = db.execute("SELECT done FROM ideas WHERE id=?", (idea_id,)).fetchone()
    if row:
        db.execute("UPDATE ideas SET done=? WHERE id=?", (0 if row["done"] == 1 else 1, idea_id))
        db.commit()
    return redirect(url_for("ideas_page"))


@app.route("/ideas/delete/<int:idea_id>", methods=["POST"])
def delete_idea(idea_id):
    db = get_db()
    row = db.execute("SELECT text FROM ideas WHERE id=?", (idea_id,)).fetchone()
    if row:
        db.execute("DELETE FROM tasks WHERE text=?", (row["text"],))
    db.execute("DELETE FROM ideas WHERE id=?", (idea_id,))
    db.commit()
    return redirect(url_for("ideas_page"))


@app.route("/ideas/clear_completed", methods=["POST"])
def clear_completed_ideas():
    db = get_db()
    db.execute("DELETE FROM ideas WHERE done=1")
    db.commit()
    return redirect(url_for("ideas_page"))


# ---------------- AI Chat ----------------
@app.route("/chat", methods=["GET"])
def chat_page():
    return render_template("chat.html")


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Proxy to OpenAI, returns AI response and optionally task suggestions."""
    import json
    try:
        import urllib.request
        data = request.get_json()
        messages = data.get("messages", [])
        api_key = data.get("api_key") or OPENAI_API_KEY

        if not api_key:
            return jsonify({"error": "No OpenAI API key provided. Add it in the chat settings."}), 400

        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        system_prompt = f"""You are a smart productivity assistant embedded in a To-Do + Eisenhower Matrix planner app.
Today's date is {today_str}. Use this when the user mentions "today", "tomorrow", "this week", etc.

Your job:
1. Help users clarify vague problems into concrete, actionable tasks.
2. If the user's request is broad or unclear, ask ONE focused clarifying question.
3. Once you understand, break the problem into specific tasks (max 5-7 subtasks).
4. For each task, suggest a priority label: "important+urgent", "important", "urgent", or "none".
5. Suggest a deadline if appropriate (YYYY-MM-DD format).

When you have enough info to create tasks, output a JSON block at the END of your message like:
<tasks>
[
  {{"text": "Task description", "label": "important+urgent", "deadline": "{today_str}"}},
  {{"text": "Another task", "label": "important", "deadline": null}}
]
</tasks>

Be conversational, warm, and brief. Don't over-explain. Ask at most one question per turn."""

        payload = json.dumps({
            "model": "gpt-4o-mini",
            "messages": [{"role": "system", "content": system_prompt}] + messages,
            "max_tokens": 800,
            "temperature": 0.7
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        content = result["choices"][0]["message"]["content"]

        # Extract tasks if present
        tasks = []
        if "<tasks>" in content and "</tasks>" in content:
            start = content.index("<tasks>") + len("<tasks>")
            end = content.index("</tasks>")
            tasks_json = content[start:end].strip()
            try:
                tasks = json.loads(tasks_json)
            except Exception:
                tasks = []
            # Clean message for display
            content = content[:content.index("<tasks>")].strip()

        return jsonify({"reply": content, "tasks": tasks})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/add_tasks_from_chat", methods=["POST"])
def add_tasks_from_chat():
    """Batch-add tasks suggested by AI."""
    data = request.get_json()
    tasks = data.get("tasks", [])
    db = get_db()

    def label_to_flags_str(label):
        label = (label or "").lower()
        if "important" in label and "urgent" in label:
            return 1, 1
        if "important" in label:
            return 1, 0
        if "urgent" in label:
            return 0, 1
        return 0, 0

    for t in tasks:
        text = t.get("text", "").strip()
        deadline = t.get("deadline") or None
        label = t.get("label", "none")
        if not text:
            continue
        imp, urg = label_to_flags_str(label)
        db.execute(
            "INSERT INTO tasks (text, done, deadline, source, created_at_utc) VALUES (?, 0, ?, 'ai', ?)",
            (text, deadline, now_utc_iso()),
        )
        db.execute(
            "INSERT INTO ideas (text, important, urgent, done, deadline, created_at_utc) VALUES (?, ?, ?, 0, ?, ?)",
            (text, imp, urg, deadline, now_utc_iso()),
        )
    db.commit()
    return jsonify({"added": len(tasks)})


# ---------------- Shutdown ----------------
@app.route("/shutdown", methods=["POST"])
def shutdown():
    os.kill(os.getpid(), signal.SIGTERM)
    return ("Shutting down...", 200)


def open_browser_once():
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    open_browser_once()
    app.run(debug=True, host=HOST, port=PORT)