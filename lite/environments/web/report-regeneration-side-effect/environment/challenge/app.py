from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request

app = Flask(__name__)

DATABASE = "/tmp/ledgerlight-reporting.db"
EVIDENCE_BUNDLE_ID = "LL-EVIDENCE-2026Q2-8F31C2"
SESSIONS: dict[str, str] = {}


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
            role TEXT NOT NULL,
            display_name TEXT NOT NULL
        );

        CREATE TABLE reports (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            owner_team TEXT NOT NULL,
            status TEXT NOT NULL,
            artifact TEXT NOT NULL,
            logs TEXT NOT NULL,
            generated_at INTEGER NOT NULL,
            generation_count INTEGER NOT NULL
        );
        """
    )
    db.execute(
        """
        INSERT INTO users (id, username, password_hash, role, display_name)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            1,
            "audit.viewer@ledgerlight.test",
            hash_password("ReadOnly!234"),
            "viewer",
            "Ledgerlight Audit Viewer",
        ),
    )
    db.executemany(
        """
        INSERT INTO reports (
            id, title, owner_team, status, artifact, logs, generated_at, generation_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "rpt-access-2026q2",
                "Q2 Privileged Access Review",
                "identity-governance",
                "failed",
                "Q2 Privileged Access Review\nNo artifact was produced for this run.\n",
                "\n".join(
                    [
                        "2026-06-30T22:00:04Z scheduled access review started",
                        "2026-06-30T22:00:07Z loaded 184 active grants",
                        "2026-06-30T22:00:31Z source snapshot lease expired",
                        "2026-06-30T22:00:31Z run failed before evidence bundle assembly",
                    ]
                ),
                1782856831,
                3,
            ),
            (
                "rpt-vendor-risk",
                "Vendor Risk Summary",
                "procurement",
                "complete",
                "Vendor Risk Summary\nReview period: 2026-Q2\nOpen findings: 3\n",
                "2026-05-24T14:02:01Z generated vendor risk artifact",
                1779631321,
                1,
            ),
        ],
    )
    db.commit()
    db.close()


def require_user(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify({"error": "Authentication required"}), 401
        token = header.split(" ", 1)[1]
        username = SESSIONS.get(token)
        if username is None:
            return jsonify({"error": "Invalid token"}), 403
        user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user is None:
            return jsonify({"error": "User not found"}), 404
        return func(user, *args, **kwargs)

    return wrapper


def report_summary(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "title": row["title"],
        "owner_team": row["owner_team"],
        "status": row["status"],
        "generated_at": row["generated_at"],
        "generation_count": row["generation_count"],
        "links": {
            "artifact": f"/api/reports/{row['id']}/artifact",
            "logs": f"/api/reports/{row['id']}/logs",
            "regenerate": f"/api/reports/{row['id']}/regenerate",
        },
    }


@app.after_request
def add_headers(response):
    response.headers["X-Ledgerlight-Build"] = "reports-2026.05.9"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "ledgerlight-reporting"})


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
    token = "sess_" + secrets.token_urlsafe(24)
    SESSIONS[token] = user["username"]
    return jsonify(
        {
            "token": token,
            "user": {
                "username": user["username"],
                "role": user["role"],
                "display_name": user["display_name"],
            },
        }
    )


@app.route("/api/reports")
@require_user
def reports(user: sqlite3.Row):
    rows = get_db().execute("SELECT * FROM reports ORDER BY id").fetchall()
    return jsonify({"reports": [report_summary(row) for row in rows]})


@app.route("/api/reports/<report_id>")
@require_user
def report_detail(user: sqlite3.Row, report_id: str):
    row = get_db().execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if row is None:
        return jsonify({"error": "Report not found"}), 404
    result = report_summary(row)
    if row["status"] == "failed":
        result["failure_reason"] = "source snapshot lease expired"
    return jsonify({"report": result})


@app.route("/api/reports/<report_id>/artifact")
@require_user
def report_artifact(user: sqlite3.Row, report_id: str):
    row = get_db().execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if row is None:
        return jsonify({"error": "Report not found"}), 404
    return jsonify(
        {
            "report_id": row["id"],
            "status": row["status"],
            "artifact": row["artifact"],
        }
    )


@app.route("/api/reports/<report_id>/logs")
@require_user
def report_logs(user: sqlite3.Row, report_id: str):
    row = get_db().execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if row is None:
        return jsonify({"error": "Report not found"}), 404
    return jsonify(
        {
            "report_id": row["id"],
            "logs": row["logs"].splitlines(),
        }
    )


@app.route("/api/reports/<report_id>/regenerate", methods=["POST"])
@require_user
def regenerate_report(user: sqlite3.Row, report_id: str):
    db = get_db()
    row = db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if row is None:
        return jsonify({"error": "Report not found"}), 404

    now = int(time.time())
    if report_id == "rpt-access-2026q2":
        artifact = "\n".join(
            [
                row["title"],
                "Review period: 2026-Q2",
                "Active grants reviewed: 184",
                "Exceptions requiring attestation: 7",
                f"Evidence bundle: {EVIDENCE_BUNDLE_ID}",
                "",
            ]
        )
    else:
        artifact = "\n".join([row["title"], "Review period: 2026-Q2", "Open findings: 3", ""])
    logs = "\n".join(
        [
            row["logs"],
            f"2026-07-01T09:14:00Z rerun requested by {user['username']}",
            "2026-07-01T09:14:02Z acquired fresh source snapshot",
            "2026-07-01T09:14:04Z report artifact assembled",
            "2026-07-01T09:14:05Z run completed",
        ]
    )
    db.execute(
        """
        UPDATE reports
        SET status = 'complete', artifact = ?, logs = ?, generated_at = ?, generation_count = generation_count + 1
        WHERE id = ?
        """,
        (artifact, logs, now, report_id),
    )
    db.commit()
    return jsonify(
        {
            "accepted": True,
            "report_id": report_id,
            "status": "complete",
            "artifact_url": f"/api/reports/{report_id}/artifact",
        }
    )


if __name__ == "__main__":
    SESSIONS.clear()
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
