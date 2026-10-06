from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from flask import Flask, jsonify, request

app = Flask(__name__)

TOKEN_SECRET = os.environ.get("TOKEN_SECRET", "s3cr3t-shared-signing-key-payflow")
DEPLOY_SECRET = "pf_deploy_7c4f91a8e2d6b305"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign(signing_input: str) -> str:
    digest = hmac.new(
        TOKEN_SECRET.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _b64url(digest)


def verify_token(token: str) -> dict | None:
    if not token or token.count(".") != 2:
        return None
    header_b64, payload_b64, signature = token.split(".", 2)
    expected = _sign(f"{header_b64}.{payload_b64}")
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        header_padding = "=" * (-len(header_b64) % 4)
        payload_padding = "=" * (-len(payload_b64) % 4)
        header = json.loads(base64.urlsafe_b64decode(header_b64 + header_padding))
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + payload_padding))
    except (ValueError, json.JSONDecodeError):
        return None
    if header.get("alg") != "HS256":
        return None
    return payload


def _bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer ") :].strip()
    return ""


@app.after_request
def add_headers(response):
    response.headers["X-Payflow-Build"] = "payflow-internal-admin-2026.05.28"
    return response


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "payflow-internal-admin"})


@app.route("/")
def index():
    return jsonify({
        "service": "payflow-internal-admin",
        "host": "admin.payflow.internal",
        "endpoints": {
            "GET /api/deployments/current": "current deployment record (Bearer token required)",
        },
    })


@app.route("/api/deployments/current")
def current_deployment():
    claims = verify_token(_bearer_token())
    if claims is None:
        return jsonify({"error": "invalid or missing token"}), 401
    return jsonify({
        "application": "payments-api",
        "deployment_id": "dep-2026-05-28-4",
        "revision": "184bf2e",
        "runner_token": DEPLOY_SECRET,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
