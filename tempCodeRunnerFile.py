import os
import sqlite3
import webbrowser
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Flask, render_template, request, redirect, url_for, g

app = Flask(__name__)

DB_NAME = "todo.db"
HOST = "127.0.0.1"
PORT = 5001  # you can change to 5000 if it's free on your mac

IST = ZoneInfo("Europe/Istanbul")


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

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            created_at_utc TEXT NOT NULL
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            important INTEGER NOT NULL DEFAULT 0,
            urgent INTEGER NOT NULL DEFAULT 0,
            done INTEGER NOT NULL DEFAULT 0,
            created_at_utc TEXT NOT NULL
        )
        """
    )

    db.commit()


@app.before_request
def before_request():
    init_db()


def now_utc_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------- Time formatting (Istanbul) ----------------
@app.template_filter("istanbul_time")
def istanbul_time(utc_iso: str) -> str:
    """
    Store timestamps in UTC, display in Europe/Istanbul.
    """
    try:
        dt_utc = datetime.fromisoformat(utc_iso)
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        dt_ist = dt_utc.astimezone(IST)
        return dt_ist.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return utc_iso


# ---------------- Routes: Tasks ----------------
@app.route("/", methods=["GET"])
def tasks_page():
    filter_value = request.args.get("filter", "all")  # all | active | completed
    db = get_db()

    if filter_value == "active":
        tasks = db.execute("SELECT * FROM tasks WHERE done=0 ORDER BY id DESC").fetchall()
    elif filter_value == "completed":
        tasks = db.execute("SELECT * FROM tasks WHERE done=1 ORDER BY id DESC").fetchall()
    else:
        tasks = db.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()

    total = db.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
    remaining = db.execute("SELECT COUNT(*) AS c FROM tasks WHERE done=0").fetchone()["c"]

    return render_template(
        "tasks.html",
        tasks=tasks,
        filter_value=filter_value,
        total=total,
        remaining=remaining,
        host=HOST,
        port=PORT,
    )


@app.route("/tasks/add", methods=["POST"])
def add_task():
    text = request.form.get("text", "").strip()
    if text:
        db = get_db()
        db.execute(
            "INSERT INTO tasks (text, done, created_at_utc) VALUES (?, 0, ?)",
            (text, now_utc_iso()),
        )
        db.commit()
    return redirect(url_for("tasks_page", filter=request.args.get("filter", "all")))


@app.route("/tasks/toggle/<int:task_id>", methods=["POST"])
def toggle_task(task_id: int):
    db = get_db()
    row = db.execute("SELECT done FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is not None:
        new_done = 0 if row["done"] == 1 else 1
        db.execute("UPDATE tasks SET done=? WHERE id=?", (new_done, task_id))
        db.commit()
    return redirect(url_for("tasks_page", filter=request.args.get("filter", "all")))


@app.route("/tasks/delete/<int:task_id>", methods=["POST"])
def delete_task(task_id: int):
    db = get_db()
    db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    db.commit()
    return redirect(url_for("tasks_page", filter=request.args.get("filter", "all")))


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
    ideas = db.execute("SELECT * FROM ideas ORDER BY id DESC").fetchall()

    quadrants = {
        "do_now": [],        # important & urgent
        "schedule": [],      # important & not urgent
        "delegate": [],      # not important & urgent
        "eliminate": [],     # not important & not urgent
    }

    for idea in ideas:
        imp = bool(idea["important"])
        urg = bool(idea["urgent"])

        if imp and urg:
            quadrants["do_now"].append(idea)
        elif imp and not urg:
            quadrants["schedule"].append(idea)
        elif (not imp) and urg:
            quadrants["delegate"].append(idea)
        else:
            quadrants["eliminate"].append(idea)

    total = db.execute("SELECT COUNT(*) AS c FROM ideas").fetchone()["c"]
    remaining = db.execute("SELECT COUNT(*) AS c FROM ideas WHERE done=0").fetchone()["c"]

    return render_template(
        "ideas.html",
        quadrants=quadrants,
        total=total,
        remaining=remaining,
        host=HOST,
        port=PORT,
    )


def label_to_flags(label: str) -> tuple[int, int]:
    """
    label: none | important | urgent | both
    """
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
    if text:
        important, urgent = label_to_flags(label)
        db = get_db()
        db.execute(
            "INSERT INTO ideas (text, important, urgent, done, created_at_utc) VALUES (?, ?, ?, 0, ?)",
            (text, important, urgent, now_utc_iso()),
        )
        db.commit()
    return redirect(url_for("ideas_page"))


@app.route("/ideas/toggle/<int:idea_id>", methods=["POST"])
def toggle_idea(idea_id: int):
    db = get_db()
    row = db.execute("SELECT done FROM ideas WHERE id=?", (idea_id,)).fetchone()
    if row is not None:
        new_done = 0 if row["done"] == 1 else 1
        db.execute("UPDATE ideas SET done=? WHERE id=?", (new_done, idea_id))
        db.commit()
    return redirect(url_for("ideas_page"))


@app.route("/ideas/delete/<int:idea_id>", methods=["POST"])
def delete_idea(idea_id: int):
    db = get_db()
    db.execute("DELETE FROM ideas WHERE id=?", (idea_id,))
    db.commit()
    return redirect(url_for("ideas_page"))


@app.route("/ideas/clear_completed", methods=["POST"])
def clear_completed_ideas():
    db = get_db()
    db.execute("DELETE FROM ideas WHERE done=1")
    db.commit()
    return redirect(url_for("ideas_page"))


# ---------------- Close / Shutdown ----------------
@app.route("/shutdown", methods=["POST"])
def shutdown():
    func = request.environ.get("werkzeug.server.shutdown")
    if func is None:
        return ("Not running with the Werkzeug dev server.", 400)
    func()
    return ("OK", 200)


# ---------------- Auto-open browser ----------------
def open_browser_once():
    url = f"http://{HOST}:{PORT}"
    # open only in the reloader child process (prevents double tabs)
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        webbrowser.open(url)


if __name__ == "__main__":
    open_browser_once()
    app.run(debug=True, host=HOST, port=PORT)