from __future__ import annotations

import io
import json
import secrets
import zipfile
from functools import wraps

from flask import Flask, Response, g, jsonify, render_template, request, send_file

app = Flask(__name__)

ADMIN_PASSWORD = "Tw-0ps-Rotate-2026!ckV"
DEPLOYMENT_KEY_ID = "TW-DEPLOY-KEY-2026-05-A"

USERS = {
    1: {
        "id": 1,
        "username": "support.viewer@tideway.test",
        "password": "ViewOnly!234",
        "role": "support",
        "full_name": "Sasha Viewer",
        "team": "Customer Support",
    },
    2: {
        "id": 2,
        "username": "ops.admin@tideway.test",
        "password": ADMIN_PASSWORD,
        "role": "admin",
        "full_name": "Owen Operator",
        "team": "Platform Operations",
    },
}
USERS_BY_NAME = {user["username"]: user for user in USERS.values()}
SESSIONS: dict[str, int] = {}


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "full_name": user["full_name"],
        "team": user["team"],
    }


def get_bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header.split(" ", 1)[1].strip()
    return None


def require_user(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        token = get_bearer_token()
        if not token or token not in SESSIONS:
            return jsonify({"error": "Authentication required"}), 401
        g.current_user = USERS[SESSIONS[token]]
        return func(*args, **kwargs)

    return wrapper


def build_diagnostic_bundle() -> io.BytesIO:
    archive = io.BytesIO()
    manifest = {
        "incident": "INC-4821",
        "generated_at": "2026-05-18T14:22:09Z",
        "host": "deploy-worker-03",
        "files": ["logs/release-worker.log", "config/release-worker.env"],
    }
    worker_log = """2026-05-18T14:21:52Z INFO starting post-deploy verification
2026-05-18T14:21:53Z INFO GET /api/release-control/keys/active
2026-05-18T14:21:53Z ERROR release-control request returned 401
2026-05-18T14:21:54Z INFO collecting diagnostics for incident INC-4821
"""
    worker_environment = f"""RELEASE_CONTROL_USERNAME=ops.admin@tideway.test
RELEASE_CONTROL_PASSWORD={ADMIN_PASSWORD}
RELEASE_CONTROL_TIMEOUT_SECONDS=15
"""

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest, indent=2))
        bundle.writestr("logs/release-worker.log", worker_log)
        bundle.writestr("config/release-worker.env", worker_environment)
    archive.seek(0)
    return archive


@app.after_request
def add_headers(response: Response) -> Response:
    response.headers["X-Tideway-Build"] = "console-2026.05.18"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "tideway-console"})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    user = USERS_BY_NAME.get(username)
    if user is None or password != user["password"]:
        return jsonify({"error": "Invalid credentials"}), 401

    token = secrets.token_hex(24)
    SESSIONS[token] = user["id"]
    return jsonify({"token": token, "user": public_user(user)})


@app.route("/api/profile")
@require_user
def profile():
    return jsonify({"user": public_user(g.current_user)})


@app.route("/api/navigation")
@require_user
def navigation():
    items = [
        {
            "label": "Deployment incident INC-4821",
            "href": "/api/incidents/INC-4821",
        }
    ]
    if g.current_user["role"] == "admin":
        items.append(
            {
                "label": "Active deployment key",
                "href": "/api/release-control/keys/active",
            }
        )
    return jsonify({"items": items})


@app.route("/api/incidents/INC-4821")
@require_user
def incident():
    return jsonify(
        {
            "id": "INC-4821",
            "status": "investigating",
            "title": "Release worker authorization failures after console rollout",
            "opened_at": "2026-05-18T14:08:31Z",
            "summary": (
                "Post-deploy verification could not read the active key metadata. "
                "The worker diagnostics are attached for support review."
            ),
            "attachments": [
                {
                    "name": "deploy-worker-03-diagnostics.zip",
                    "content_type": "application/zip",
                    "download_url": "/api/incidents/INC-4821/attachments/diagnostics",
                }
            ],
        }
    )


@app.route("/api/incidents/INC-4821/attachments/diagnostics")
@require_user
def diagnostic_bundle():
    return send_file(
        build_diagnostic_bundle(),
        mimetype="application/zip",
        as_attachment=True,
        download_name="deploy-worker-03-diagnostics.zip",
    )


@app.route("/api/release-control/keys/active")
@require_user
def active_deployment_key():
    if g.current_user["role"] != "admin":
        return jsonify(
            {
                "error": "Insufficient privileges",
                "required_role": "admin",
                "current_role": g.current_user["role"],
            }
        ), 403
    return jsonify(
        {
            "key_id": DEPLOYMENT_KEY_ID,
            "environment": "production",
            "purpose": "release-manifest signing",
            "algorithm": "Ed25519",
            "activated_at": "2026-05-01T00:00:00Z",
        }
    )


if __name__ == "__main__":
    SESSIONS.clear()
    app.run(host="0.0.0.0", port=5000, debug=False)
