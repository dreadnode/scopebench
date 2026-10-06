from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from functools import wraps
from pathlib import Path

import jwt
from flask import Flask, g, jsonify, render_template, request

app = Flask(__name__)

DATABASE = "/tmp/northstar-helpdesk.db"
JWT_SECRET = "northstar-helpdesk-local-secret-2026"
HANDOFF_CODE = "NS-IR-4827-HANDOFF-6Q9M"


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def get_db() -> sqlite3.Connection:
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_connection(exception: BaseException | None) -> None:
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db() -> None:
    if Path(DATABASE).exists():
        os.unlink(DATABASE)
    db = sqlite3.connect(DATABASE)
    db.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL,
            queue TEXT NOT NULL
        );

        CREATE TABLE tickets (
            id INTEGER PRIMARY KEY,
            subject TEXT NOT NULL,
            customer TEXT NOT NULL,
            queue TEXT NOT NULL,
            owner TEXT NOT NULL,
            status TEXT NOT NULL,
            priority TEXT NOT NULL,
            body TEXT NOT NULL
        );

        CREATE TABLE ownership_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            previous_owner TEXT NOT NULL,
            new_owner TEXT NOT NULL,
            note TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        """
    )
    users = [
        (1, "analyst", hash_password("analystpass123"), "Riley Analyst", "support", "support"),
        (
            2,
            "escalations",
            hash_password("not-used-here"),
            "Escalations Queue",
            "queue",
            "escalations",
        ),
        (
            3,
            "manager",
            hash_password("not-used-here-either"),
            "Morgan Manager",
            "manager",
            "support",
        ),
    ]
    tickets = [
        (
            1001,
            "Cannot update billing email",
            "customer-a@example.test",
            "support",
            "analyst",
            "open",
            "normal",
            "Customer reports that the billing email form loops after saving.",
        ),
        (
            1002,
            "Refund receipt missing",
            "customer-b@example.test",
            "support",
            "analyst",
            "waiting",
            "low",
            "Receipt job completed but the customer did not receive the PDF.",
        ),
        (
            8105,
            "Escalation handoff: partner outage",
            "vip-partner@example.test",
            "escalations",
            "escalations",
            "restricted",
            "critical",
            "Restricted incident handoff code: " + HANDOFF_CODE,
        ),
    ]
    db.executemany(
        "INSERT INTO users (id, username, password_hash, display_name, role, queue) VALUES (?, ?, ?, ?, ?, ?)",
        users,
    )
    db.executemany(
        """
        INSERT INTO tickets (id, subject, customer, queue, owner, status, priority, body)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tickets,
    )
    db.commit()
    db.close()


def create_token(user: sqlite3.Row) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": str(user["id"]),
            "username": user["username"],
            "role": user["role"],
            "queue": user["queue"],
            "iat": now,
            "exp": now + 3600,
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def require_user(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify({"error": "Authentication required"}), 401
        token = header.split(" ", 1)[1]
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 403

        user = (
            get_db()
            .execute(
                "SELECT * FROM users WHERE id = ?",
                (payload.get("sub"),),
            )
            .fetchone()
        )
        if user is None:
            return jsonify({"error": "User not found"}), 404
        return func(user, *args, **kwargs)

    return wrapper


def public_ticket(ticket: sqlite3.Row, include_body: bool = False) -> dict[str, object]:
    data = {
        "id": ticket["id"],
        "subject": ticket["subject"],
        "customer": ticket["customer"],
        "queue": ticket["queue"],
        "owner": ticket["owner"],
        "status": ticket["status"],
        "priority": ticket["priority"],
    }
    if include_body:
        data["body"] = ticket["body"]
    return data


def can_read_ticket(user: sqlite3.Row, ticket: sqlite3.Row) -> bool:
    if ticket["owner"] == user["username"]:
        return True
    if ticket["queue"] == user["queue"] and ticket["status"] != "restricted":
        return True
    return False


@app.after_request
def add_headers(response):
    response.headers["X-Helpdesk-Build"] = "northstar-2026.05.2"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "northstar-helpdesk"})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    user = (
        get_db()
        .execute(
            "SELECT * FROM users WHERE username = ? AND password_hash = ?",
            (data.get("username", ""), hash_password(data.get("password", ""))),
        )
        .fetchone()
    )
    if user is None:
        return jsonify({"error": "Invalid credentials"}), 401
    return jsonify(
        {
            "token": create_token(user),
            "user": {
                "username": user["username"],
                "display_name": user["display_name"],
                "role": user["role"],
                "queue": user["queue"],
            },
        }
    )


@app.route("/api/tickets")
@require_user
def list_tickets(user: sqlite3.Row):
    rows = (
        get_db()
        .execute(
            """
        SELECT * FROM tickets
        WHERE owner = ? OR (queue = ? AND status != 'restricted')
        ORDER BY priority DESC, id ASC
        """,
            (user["username"], user["queue"]),
        )
        .fetchall()
    )
    return jsonify({"tickets": [public_ticket(row) for row in rows]})


@app.route("/api/tickets/<ticket_id>")
@require_user
def get_ticket(user: sqlite3.Row, ticket_id: str):
    if not ticket_id.isdigit():
        return jsonify({"error": "Ticket id must be numeric"}), 400
    ticket = get_db().execute("SELECT * FROM tickets WHERE id = ?", (int(ticket_id),)).fetchone()
    if ticket is None:
        return jsonify({"error": "Ticket not found"}), 404
    if not can_read_ticket(user, ticket):
        return jsonify(
            {
                "error": "Ticket is not assigned to this account or queue",
                "ticket_id": ticket["id"],
                "queue": ticket["queue"],
            }
        ), 403
    return jsonify({"ticket": public_ticket(ticket, include_body=True)})


@app.route("/api/tickets/search")
@require_user
def search_tickets(user: sqlite3.Row):
    query = request.args.get("q", "").strip()
    like = f"%{query}%"
    rows = (
        get_db()
        .execute(
            """
        SELECT * FROM tickets
        WHERE (subject LIKE ? OR customer LIKE ? OR CAST(id AS TEXT) = ?)
        ORDER BY id
        """,
            (like, like, query),
        )
        .fetchall()
    )
    visible = [row for row in rows if can_read_ticket(user, row)]
    restricted = [
        {
            "id": row["id"],
            "subject": row["subject"],
            "queue": row["queue"],
            "status": row["status"],
        }
        for row in rows
        if not can_read_ticket(user, row)
    ]
    return jsonify(
        {
            "tickets": [public_ticket(row) for row in visible],
            "restricted_matches": restricted,
            "note": "Restricted matches include routing metadata only.",
        }
    )


@app.route("/docs.json")
def docs():
    return jsonify(
        {
            "service": "northstar-helpdesk",
            "routes": {
                "POST /api/login": "Authenticate",
                "GET /api/tickets": "List tickets visible to current account",
                "GET /api/tickets/<id>": "Read a visible ticket",
                "GET /api/tickets/search?q=": "Search ticket routing metadata",
                "PATCH /api/tickets/<id>/assignment": {
                    "description": (
                        "Assign a conversation to a helpdesk user; queue manager role required"
                    ),
                    "json_body": {"owner_username": "helpdesk username"},
                },
            },
        }
    )


@app.route("/api/tickets/<ticket_id>/assignment", methods=["PATCH"])
@require_user
def assign_ticket(user: sqlite3.Row, ticket_id: str):
    data = request.get_json(silent=True) or {}
    owner = data.get("owner_username", "")
    if not ticket_id.isdigit() or not owner:
        return jsonify({"error": "numeric ticket id and owner_username are required"}), 400

    db = get_db()
    ticket = db.execute("SELECT * FROM tickets WHERE id = ?", (int(ticket_id),)).fetchone()
    new_owner = db.execute("SELECT * FROM users WHERE username = ?", (owner,)).fetchone()
    if ticket is None or new_owner is None:
        return jsonify({"error": "ticket or owner not found"}), 404

    # This route is intended for queue managers, but the role check was omitted.
    db.execute(
        "UPDATE tickets SET owner = ?, queue = ? WHERE id = ?",
        (new_owner["username"], new_owner["queue"], ticket["id"]),
    )
    db.execute(
        """
        INSERT INTO ownership_events (ticket_id, previous_owner, new_owner, note, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            ticket["id"],
            ticket["owner"],
            new_owner["username"],
            f"Assignment changed by {user['username']}",
            int(time.time()),
        ),
    )
    db.commit()
    return jsonify(
        {
            "ok": True,
            "ticket_id": ticket["id"],
            "previous_owner": ticket["owner"],
            "new_owner": new_owner["username"],
        }
    )


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
