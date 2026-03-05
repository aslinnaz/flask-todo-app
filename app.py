import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, g

app = Flask(__name__)
DB_NAME = "todo.db"

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
            created_at TEXT NOT NULL
        )
    """)
    db.commit()

@app.before_request
def before_request():
    init_db()

@app.route("/", methods=["GET"])
def index():
    filter_value = request.args.get("filter", "all")  # all | active | completed
    db = get_db()

    if filter_value == "active":
        rows = db.execute("SELECT * FROM tasks WHERE done=0 ORDER BY id DESC").fetchall()
    elif filter_value == "completed":
        rows = db.execute("SELECT * FROM tasks WHERE done=1 ORDER BY id DESC").fetchall()
    else:
        rows = db.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()

    total = db.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
    remaining = db.execute("SELECT COUNT(*) AS c FROM tasks WHERE done=0").fetchone()["c"]

    return render_template(
        "index.html",
        tasks=rows,
        filter_value=filter_value,
        total=total,
        remaining=remaining
    )

@app.route("/add", methods=["POST"])
def add():
    text = request.form.get("text", "").strip()
    if text:
        db = get_db()
        db.execute(
            "INSERT INTO tasks (text, done, created_at) VALUES (?, 0, ?)",
            (text, datetime.utcnow().isoformat(timespec="seconds"))
        )
        db.commit()
    return redirect(url_for("index", filter=request.args.get("filter", "all")))

@app.route("/toggle/<int:task_id>", methods=["POST"])
def toggle(task_id: int):
    db = get_db()
    task = db.execute("SELECT done FROM tasks WHERE id=?", (task_id,)).fetchone()
    if task is not None:
        new_done = 0 if task["done"] == 1 else 1
        db.execute("UPDATE tasks SET done=? WHERE id=?", (new_done, task_id))
        db.commit()
    return redirect(url_for("index", filter=request.args.get("filter", "all")))

@app.route("/delete/<int:task_id>", methods=["POST"])
def delete(task_id: int):
    db = get_db()
    db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    db.commit()
    return redirect(url_for("index", filter=request.args.get("filter", "all")))

@app.route("/clear_completed", methods=["POST"])
def clear_completed():
    db = get_db()
    db.execute("DELETE FROM tasks WHERE done=1")
    db.commit()
    return redirect(url_for("index", filter=request.args.get("filter", "all")))

if __name__ == "__main__":
    app.run(debug=True)