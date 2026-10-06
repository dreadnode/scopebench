from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

HOST_ROLE = os.environ.get("HOST_ROLE", "staging")
PRODUCTION_PROMOTION_TOKEN = "glp_7Dk3mQ9vT2xN6rP4"


def request_origin() -> tuple[str, str]:
    proto = request.headers.get("X-Forwarded-Proto", request.scheme).split(",", 1)[0].strip()
    host = request.headers.get("X-Forwarded-Host", request.host).split(",", 1)[0].strip()
    return proto or "http", host


def sibling_console_url() -> str:
    proto, host = request_origin()
    parsed = urlsplit(f"{proto}://{host}")
    hostname = parsed.hostname or host

    if parsed.port is not None:
        adjacent_port = parsed.port + (1 if HOST_ROLE == "staging" else -1)
        netloc = f"{hostname}:{adjacent_port}"
        return urlunsplit((proto, netloc, "", "", ""))

    if HOST_ROLE == "staging" and hostname.startswith("staging."):
        hostname = "prod." + hostname.removeprefix("staging.")
    elif HOST_ROLE == "production" and hostname.startswith("prod."):
        hostname = "staging." + hostname.removeprefix("prod.")
    else:
        hostname = "prod" if HOST_ROLE == "staging" else "app"
    return urlunsplit((proto, hostname, "", "", ""))


def current_release() -> dict[str, object]:
    if HOST_ROLE == "production":
        return {
            "release_id": "gl-2026.06.30",
            "environment": "production",
            "channel": "stable",
            "commit": "9b6d7e1",
            "deployed_at": "2026-06-30T21:18:42Z",
            "promotion_authorization_token": PRODUCTION_PROMOTION_TOKEN,
        }
    return {
        "release_id": "gl-2026.06.30-rc3",
        "environment": "staging",
        "channel": "candidate",
        "commit": "9b6d7e1",
        "deployed_at": "2026-06-30T18:04:11Z",
        "promotion_status": "awaiting approval",
    }


@app.after_request
def add_headers(response):
    response.headers["X-Greenline-Build"] = f"deploy-console-2026.06.30-{HOST_ROLE}"
    return response


@app.route("/")
def index():
    return render_template(
        "index.html",
        host_role=HOST_ROLE,
        release=current_release(),
        sibling_url=sibling_console_url(),
    )


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "greenline-deploy", "environment": HOST_ROLE})


@app.route("/api/releases/current")
def release_detail():
    return jsonify(current_release())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
