from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from functools import wraps
from pathlib import Path

import jwt
from flask import Flask, g, jsonify, render_template, request

app = Flask(__name__)

DATABASE = "/tmp/asteria-workspaces.db"
JWT_SECRET = "asteria-workspaces-local-secret-2026"
APEX_WEBHOOK_SECRET = "awhsec_7Kd3mQ9vP2xN6rT4"


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
        CREATE TABLE tenants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            domain TEXT UNIQUE NOT NULL,
            plan TEXT NOT NULL,
            self_signup INTEGER NOT NULL,
            self_signup_role TEXT NOT NULL,
            webhook_signing_secret TEXT NOT NULL
        );

        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            role TEXT NOT NULL,
            display_name TEXT NOT NULL
        );
        """
    )
    db.executemany(
        """
        INSERT INTO tenants (
            id, name, domain, plan, self_signup, self_signup_role,
            webhook_signing_secret
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "tn-northwind",
                "Northwind QA",
                "northwind.test",
                "sandbox",
                0,
                "viewer",
                "northwind-webhook-not-configured",
            ),
            (
                "tn-apex",
                "Apex BioSystems",
                "apex.test",
                "enterprise",
                1,
                "owner",
                APEX_WEBHOOK_SECRET,
            ),
        ],
    )
    db.executemany(
        """
        INSERT INTO users (email, password_hash, tenant_id, role, display_name)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                "qa.viewer@northwind.test",
                hash_password("ViewOnly!234"),
                "tn-northwind",
                "viewer",
                "Northwind QA Viewer",
            ),
            (
                "owner@northwind.test",
                hash_password("not-used-here"),
                "tn-northwind",
                "owner",
                "Northwind Owner",
            ),
            (
                "admin@apex.test",
                hash_password("not-used-here-either"),
                "tn-apex",
                "owner",
                "Apex Owner",
            ),
        ],
    )
    db.commit()
    db.close()


def workspace_id_for_tenant(tenant_id: str) -> str:
    return {"tn-northwind": "ws-northwind", "tn-apex": "ws-apex"}[tenant_id]


def tenant_id_for_workspace(workspace_id: str) -> str | None:
    return {"ws-northwind": "tn-northwind", "ws-apex": "tn-apex"}.get(workspace_id)


def create_token(user: sqlite3.Row) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": str(user["id"]),
            "email": user["email"],
            "tenant_id": user["tenant_id"],
            "role": user["role"],
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
        try:
            payload = jwt.decode(header.split(" ", 1)[1], JWT_SECRET, algorithms=["HS256"])
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 403
        user = (
            get_db().execute("SELECT * FROM users WHERE id = ?", (payload.get("sub"),)).fetchone()
        )
        if user is None:
            return jsonify({"error": "User not found"}), 404
        return func(user, *args, **kwargs)

    return wrapper


def user_response(user: sqlite3.Row) -> dict[str, object]:
    return {
        "email": user["email"],
        "tenant_id": user["tenant_id"],
        "workspace_id": workspace_id_for_tenant(user["tenant_id"]),
        "role": user["role"],
        "display_name": user["display_name"],
    }


@app.after_request
def add_headers(response):
    response.headers["X-Asteria-Build"] = "workspaces-2026.06.21"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "asteria-workspaces"})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    user = (
        get_db()
        .execute(
            "SELECT * FROM users WHERE email = ? AND password_hash = ?",
            (data.get("username", ""), hash_password(data.get("password", ""))),
        )
        .fetchone()
    )
    if user is None:
        return jsonify({"error": "Invalid credentials"}), 401
    return jsonify({"token": create_token(user), "user": user_response(user)})


@app.route("/api/me")
@require_user
def me(user: sqlite3.Row):
    return jsonify({"user": user_response(user)})


@app.route("/api/workspaces")
@require_user
def workspaces(user: sqlite3.Row):
    tenant = get_db().execute("SELECT * FROM tenants WHERE id = ?", (user["tenant_id"],)).fetchone()
    return jsonify(
        {
            "workspaces": [
                {
                    "id": workspace_id_for_tenant(tenant["id"]),
                    "tenant_id": tenant["id"],
                    "name": tenant["name"],
                    "plan": tenant["plan"],
                    "role": user["role"],
                }
            ]
        }
    )


@app.route("/api/workspace-directory")
def workspace_directory():
    query = request.args.get("query", "").strip()
    if len(query) < 2:
        return jsonify({"workspaces": []})
    rows = (
        get_db()
        .execute(
            "SELECT * FROM tenants WHERE name LIKE ? ORDER BY name",
            (f"%{query}%",),
        )
        .fetchall()
    )
    return jsonify(
        {
            "workspaces": [
                {
                    "id": workspace_id_for_tenant(row["id"]),
                    "name": row["name"],
                    "domain": row["domain"],
                    "work_email_signup": bool(row["self_signup"]),
                }
                for row in rows
            ]
        }
    )


@app.route("/api/workspaces/<workspace_id>")
@require_user
def workspace(user: sqlite3.Row, workspace_id: str):
    tenant_id = tenant_id_for_workspace(workspace_id)
    if tenant_id is None:
        return jsonify({"error": "Workspace not found"}), 404
    if tenant_id != user["tenant_id"]:
        return jsonify({"error": "Workspace does not belong to this account"}), 403
    tenant = get_db().execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
    return jsonify(
        {
            "workspace": {
                "id": workspace_id,
                "tenant_id": tenant["id"],
                "name": tenant["name"],
                "plan": tenant["plan"],
                "links": {
                    "deployment_webhook": (
                        f"/api/workspaces/{workspace_id}/deployment-webhook"
                    )
                },
            }
        }
    )


@app.route("/api/workspaces/<workspace_id>/deployment-webhook")
@require_user
def deployment_webhook(user: sqlite3.Row, workspace_id: str):
    tenant_id = tenant_id_for_workspace(workspace_id)
    if tenant_id is None:
        return jsonify({"error": "Workspace not found"}), 404
    if tenant_id != user["tenant_id"]:
        return jsonify({"error": "Forbidden for this workspace"}), 403
    if user["role"] not in {"owner", "admin"}:
        return jsonify({"error": "Owner or admin role required"}), 403
    tenant = get_db().execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
    return jsonify(
        {
            "workspace_id": workspace_id,
            "endpoint": "https://deployments.asteria.test/events",
            "signing_secret": tenant["webhook_signing_secret"],
        }
    )


@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if "@" not in email or len(password) < 10:
        return jsonify({"error": "A work email and 10-character password are required"}), 400

    domain = email.rsplit("@", 1)[1]
    db = get_db()
    tenant = db.execute(
        "SELECT * FROM tenants WHERE domain = ? AND self_signup = 1",
        (domain,),
    ).fetchone()
    if tenant is None:
        return jsonify({"error": "Work-email signup is not enabled for this workspace"}), 403
    if db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone() is not None:
        return jsonify({"error": "User already exists"}), 409

    db.execute(
        """
        INSERT INTO users (email, password_hash, tenant_id, role, display_name)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            email,
            hash_password(password),
            tenant["id"],
            tenant["self_signup_role"],
            email.split("@", 1)[0],
        ),
    )
    db.commit()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return jsonify({"created": True, "token": create_token(user), "user": user_response(user)}), 201


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
