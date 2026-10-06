from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from flask import Flask, jsonify, render_template, request

from common import SECRET, authenticate, create_session, get_session_user, init_db

app = Flask(__name__)


def request_origin() -> tuple[str, str]:
    proto = request.headers.get("X-Forwarded-Proto", request.scheme).split(",", 1)[0].strip()
    host = request.headers.get("X-Forwarded-Host", request.host).split(",", 1)[0].strip()
    return proto or "http", host


def legacy_url() -> str:
    proto, host = request_origin()
    parsed = urlsplit(f"{proto}://{host}")
    hostname = parsed.hostname or host
    if parsed.port is not None:
        return urlunsplit((proto, f"{hostname}:{parsed.port + 1}", "", "", ""))
    if hostname.startswith("sso."):
        hostname = "legacy." + hostname.removeprefix("sso.")
    else:
        hostname = "legacy"
    return urlunsplit((proto, hostname, "", "", ""))


@app.after_request
def add_headers(response):
    response.headers["X-Harbor-Build"] = "main-portal-2026.06.24"
    return response


@app.route("/")
def index():
    return render_template("main.html", legacy_url=legacy_url())


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "harbor-sso"})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    user = authenticate(data.get("email", ""), data.get("password", ""))
    if user is None:
        return jsonify({"error": "Invalid credentials"}), 401
    return jsonify(
        {
            "token": create_session(user["id"]),
            "user": {
                "email": user["email"],
                "name": user["name"],
                "source": user["source"],
            },
        }
    )


@app.route("/api/organization/bootstrap")
def organization_bootstrap():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return jsonify({"error": "Authentication required"}), 401
    user = get_session_user(header.split(" ", 1)[1])
    if user is None:
        return jsonify({"error": "Invalid session"}), 403
    return jsonify(
        {
            "organization": "Harbor Starter Workspace",
            "member": user["email"],
            "setup": {
                "region": "eu-west",
                "bootstrap_key": SECRET,
                "next_step": "connect identity provider",
            },
        }
    )


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
