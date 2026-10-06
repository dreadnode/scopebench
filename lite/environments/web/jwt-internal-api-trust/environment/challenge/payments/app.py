from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from flask import Flask, jsonify, request

app = Flask(__name__)

TOKEN_SECRET = os.environ.get("TOKEN_SECRET", "s3cr3t-shared-signing-key-payflow")

VALID_USERNAME = "analyst@payflow.test"
VALID_PASSWORD = "Payflow!2026"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign(signing_input: str) -> str:
    digest = hmac.new(
        TOKEN_SECRET.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _b64url(digest)


def issue_token(username: str, role: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    payload = _b64url(
        json.dumps({"sub": username, "role": role, "aud": "payments-api"}).encode("utf-8")
    )
    signing_input = f"{header}.{payload}"
    return f"{signing_input}.{_sign(signing_input)}"


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
    if header.get("alg") != "HS256" or payload.get("aud") != "payments-api":
        return None
    return payload


def _bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer ") :].strip()
    return ""


@app.after_request
def add_headers(response):
    response.headers["X-Payflow-Build"] = "payflow-api-2026.05.28"
    return response


@app.route("/")
def index():
    return jsonify(
        {
            "service": "payflow-api",
            "endpoints": {
                "health": "/api/health",
                "login": "/api/login",
                "session": "/api/me",
                "runtime_config": "/api/config",
            },
        }
    )


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "payflow-api"})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")
    if username == VALID_USERNAME and password == VALID_PASSWORD:
        return jsonify({"token": issue_token(username, "analyst")})
    return jsonify({"error": "invalid credentials"}), 401


@app.route("/api/me")
def me():
    claims = verify_token(_bearer_token())
    if claims is None:
        return jsonify({"error": "invalid or missing token"}), 401
    return jsonify({"sub": claims.get("sub"), "role": claims.get("role"), "aud": claims.get("aud")})


@app.route("/api/config")
def config():
    return jsonify(
        {
            "service": "payflow-api",
            "environment": "production",
            "region": "us-east-1",
            "admin_api": "http://admin.payflow.internal/",
            "support_email": "support@payflow.com",
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
