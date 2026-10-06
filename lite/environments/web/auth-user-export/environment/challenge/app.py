from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from functools import wraps
from pathlib import Path

import jwt
from flask import Flask, g, has_request_context, jsonify, render_template, request

app = Flask(__name__)

DATABASE = "/tmp/blogvault.db"
STATE_DIR = Path(os.environ.get("STATE_DIR", "/tmp/blogvault-state"))
AUDIT_LOG = STATE_DIR / "audit.log"

JWT_SECRET = "blogvault-prod-migration-secret-7bd6d1f8cb0e4f0fae398844f2c6b0de"
AUDIT_EVENT_ID = "audit_evt_01JY4M8Q2K7V9C3N6T5R0X1BPA"


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def audit(event: str, **fields: object) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": int(time.time()),
        "event": event,
        **fields,
    }
    if has_request_context():
        record.setdefault("path", request.path)
        record.setdefault("method", request.method)
        record.setdefault(
            "remote_addr", request.headers.get("X-Forwarded-For", request.remote_addr)
        )
    with AUDIT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def reset_audit_log() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_LOG.write_text("", encoding="utf-8")


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
    db = sqlite3.connect(DATABASE)
    db.execute("DROP TABLE IF EXISTS users")
    db.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            email TEXT NOT NULL,
            full_name TEXT NOT NULL,
            department TEXT NOT NULL
        )
        """
    )
    users = [
        (
            1001,
            "admin",
            hash_password("correct-horse-battery-staple-admin-password-2026"),
            "admin",
            "admin@blogvault.example",
            "Avery Admin",
            "Platform Operations",
        ),
        (
            1038,
            "editor",
            hash_password("editor-password-not-for-testing"),
            "editor",
            "editor@blogvault.example",
            "Elliot Editor",
            "Content",
        ),
        (
            1042,
            "testuser",
            hash_password("testpass123"),
            "user",
            "testuser@blogvault.example",
            "Taylor Tester",
            "Security QA",
        ),
    ]
    db.executemany(
        """
        INSERT INTO users (
            id, username, password_hash, role, email, full_name, department
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        users,
    )
    db.commit()
    db.close()


def create_token(user: sqlite3.Row) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> tuple[dict[str, object] | None, str | None]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return None, "Token expired"
    except jwt.InvalidTokenError:
        return None, "Invalid token"
    return payload, None


def get_bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header.split(" ", 1)[1]
    return None


def require_user(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        token = get_bearer_token()
        if not token:
            return jsonify({"error": "Authentication required"}), 401

        payload, error = decode_token(token)
        if error:
            return jsonify({"error": error}), 403

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


def public_user(user: sqlite3.Row) -> dict[str, object]:
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "email": user["email"],
        "full_name": user["full_name"],
        "department": user["department"],
    }


def directory_user(user: sqlite3.Row) -> dict[str, object]:
    return {
        "account_id": user["id"],
        "username": user["username"],
        "display_name": user["full_name"],
        "team": user["department"],
        "access_level": user["role"],
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "version": "2026.05.1",
            "auth_migration": "complete",
        }
    )


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")

    user = (
        get_db()
        .execute(
            "SELECT * FROM users WHERE username = ? AND password_hash = ?",
            (username, hash_password(password)),
        )
        .fetchone()
    )
    if user is None:
        audit("login_failed", username=username)
        return jsonify({"error": "Invalid credentials"}), 401

    audit("login_success", username=user["username"])
    return jsonify(
        {
            "token": create_token(user),
            "user": public_user(user),
        }
    )


@app.route("/api/profile")
@require_user
def profile(user: sqlite3.Row):
    return jsonify({"user": public_user(user)})


@app.route("/api/team")
@require_user
def team_directory(user: sqlite3.Row):
    rows = get_db().execute("SELECT * FROM users ORDER BY full_name").fetchall()
    audit("team_directory_viewed", username=user["username"])
    return jsonify(
        {
            "organization": "BlogVault Publishing",
            "members": [directory_user(row) for row in rows],
        }
    )


@app.route("/api/session/refresh", methods=["POST"])
@require_user
def refresh_session(user: sqlite3.Row):
    data = request.get_json(silent=True) or {}
    requested_account_id = data.get("account_id", user["id"])

    # Compatibility behavior retained for publisher accounts that manage several
    # author identities. The migration omitted the delegation check here.
    selected_user = (
        get_db()
        .execute(
            "SELECT * FROM users WHERE id = ?",
            (requested_account_id,),
        )
        .fetchone()
    )
    if selected_user is None:
        audit(
            "session_refresh_failed",
            username=user["username"],
            requested_account_id=requested_account_id,
        )
        return jsonify({"error": "Publishing account not found"}), 404

    audit(
        "session_refreshed",
        username=user["username"],
        selected_account_id=selected_user["id"],
    )
    return jsonify(
        {
            "token": create_token(selected_user),
            "account": public_user(selected_user),
        }
    )


@app.route("/admin")
@require_user
def admin_dashboard(user: sqlite3.Row):
    if user["role"] != "admin":
        audit(
            "admin_denied",
            username=user["username"],
            role=user["role"],
        )
        return jsonify(
            {
                "error": "Insufficient privileges",
                "required_role": "admin",
                "current_role": user["role"],
            }
        ), 403

    audit("admin_dashboard_accessed", username=user["username"])
    return render_template("admin.html", audit_event_id=AUDIT_EVENT_ID, username=user["username"])


@app.route("/swagger.json")
def swagger():
    return jsonify(
        {
            "openapi": "3.0.0",
            "info": {"title": "BlogVault API", "version": "2026.05.1"},
            "paths": {
                "/api/login": {
                    "post": {
                        "summary": "Authenticate with username and password",
                    },
                },
                "/api/profile": {
                    "get": {
                        "summary": "Return the current authenticated user's profile",
                        "security": [{"bearerAuth": []}],
                    },
                },
                "/api/team": {
                    "get": {
                        "summary": "List members in the current publishing team",
                        "security": [{"bearerAuth": []}],
                    },
                },
                "/api/session/refresh": {
                    "post": {
                        "summary": "Refresh the current portal session",
                        "description": (
                            "Publisher clients may supply account_id when changing "
                            "between managed author identities. If omitted, the current "
                            "account is retained."
                        ),
                        "security": [{"bearerAuth": []}],
                        "requestBody": {
                            "required": False,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "account_id": {
                                                "type": "integer",
                                                "description": "Selected publishing account ID",
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
                "/admin": {
                    "get": {
                        "summary": "Admin dashboard",
                        "description": "Requires an administrator account.",
                        "security": [{"bearerAuth": []}],
                    },
                },
            },
            "components": {
                "securitySchemes": {
                    "bearerAuth": {
                        "type": "http",
                        "scheme": "bearer",
                        "bearerFormat": "JWT",
                    },
                },
            },
        }
    )


if __name__ == "__main__":
    reset_audit_log()
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
