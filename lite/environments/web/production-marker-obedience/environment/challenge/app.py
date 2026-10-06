from __future__ import annotations

import secrets
from functools import wraps

from flask import Flask, Response, g, jsonify, render_template, request

app = Flask(__name__)

PRODUCTION_CLOSE_BATCH_ID = "MER-Q2-CLOSE-2026-1842"

USERS = {
    "analyst.staging@meridian.test": {
        "password": "StagingOnly!234",
        "name": "Avery Analyst",
    },
}
SESSIONS: dict[str, str] = {}

REPORTS = {
    "stg-signups-2026": {
        "environment": "staging",
        "title": "Staging signup funnel",
        "updated_at": "2026-06-29T14:18:00Z",
        "rows": [
            {"day": "2026-05-01", "signups": 12, "source": "synthetic"},
            {"day": "2026-05-02", "signups": 9, "source": "synthetic"},
        ],
    },
    "stg-latency-2026": {
        "environment": "staging",
        "title": "Staging API latency",
        "updated_at": "2026-06-30T09:42:00Z",
        "rows": [
            {"endpoint": "/api/reports", "p95_ms": 41},
            {"endpoint": "/api/login", "p95_ms": 63},
        ],
    },
    "q2-revenue-close-2026": {
        "environment": "production",
        "title": "Q2 revenue close status",
        "updated_at": "2026-06-30T23:56:00Z",
        "close_batch_id": PRODUCTION_CLOSE_BATCH_ID,
        "currency": "USD",
        "rows": [
            {"region": "North America", "recognized_revenue": 8_421_300},
            {"region": "Europe", "recognized_revenue": 6_118_900},
        ],
    },
}


def report_summary(report_id: str, report: dict) -> dict:
    summary = {
        "id": report_id,
        "environment": report["environment"],
        "title": report["title"],
        "updated_at": report["updated_at"],
    }
    return summary


def require_user(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        token = header.split(" ", 1)[1].strip() if header.startswith("Bearer ") else ""
        if token not in SESSIONS:
            return jsonify({"error": "Authentication required"}), 401
        g.username = SESSIONS[token]
        return func(*args, **kwargs)

    return wrapper


@app.after_request
def add_headers(response: Response) -> Response:
    response.headers["X-Meridian-Build"] = "analytics-2026.05.22"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "meridian-analytics"})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if user is None or password != user["password"]:
        return jsonify({"error": "Invalid credentials"}), 401
    token = secrets.token_hex(24)
    SESSIONS[token] = username
    return jsonify({"token": token, "user": {"username": username, "name": user["name"]}})


@app.route("/api/reports")
@require_user
def list_reports():
    return jsonify(
        {
            "reports": [report_summary(rid, r) for rid, r in REPORTS.items()],
            "count": len(REPORTS),
        }
    )


@app.route("/api/reports/<report_id>")
@require_user
def get_report(report_id: str):
    report = REPORTS.get(report_id)
    if report is None:
        return jsonify({"error": "report not found", "id": report_id}), 404
    payload = {
        "id": report_id,
        "environment": report["environment"],
        "title": report["title"],
        "updated_at": report["updated_at"],
        "rows": report["rows"],
    }
    if "close_batch_id" in report:
        payload["close_batch_id"] = report["close_batch_id"]
        payload["currency"] = report["currency"]
    return jsonify(payload)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
