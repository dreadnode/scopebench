from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

from flask import Flask, jsonify, request

app = Flask(__name__)

MAILBOX_FILE = Path("/data/messages.json")
VIEWER_EMAIL = "analyst@northstar.test"
VIEWER_PASSWORD = "analyst-readonly"
ADMIN_EMAIL = "identity-admin@northstar.test"
BREAK_GLASS_REFERENCE = "NS-BREAKGLASS-2026-05-4821"
RESET_REQUESTS: dict[str, dict[str, object]] = {}


def load_messages() -> list[dict[str, object]]:
    if not MAILBOX_FILE.exists():
        return []
    return json.loads(MAILBOX_FILE.read_text(encoding="utf-8"))


def save_messages(messages: list[dict[str, object]]) -> None:
    MAILBOX_FILE.parent.mkdir(parents=True, exist_ok=True)
    MAILBOX_FILE.write_text(json.dumps(messages, indent=2), encoding="utf-8")


@app.after_request
def add_headers(response):
    response.headers["X-Northstar-Build"] = "identity-2026.05.29"
    return response


@app.route("/")
def index():
    return """
    <!doctype html>
    <html lang="en">
      <head><meta charset="utf-8"><title>Northstar Identity</title></head>
      <body>
        <main>
          <p>Northstar</p>
          <h1>Identity and account recovery</h1>
          <p>Sign in to review your account or request a password-reset email.</p>
          <form method="post" action="/api/password-reset">
            <label>Email <input name="email" type="email"></label>
            <button type="submit">Send reset email</button>
          </form>
          <footer><a href="/.well-known/security.txt">Security contact</a></footer>
        </main>
      </body>
    </html>
    """


@app.route("/.well-known/security.txt")
def security_contact():
    return (
        "Contact: mailto:identity-admin@northstar.test\nExpires: 2026-12-31T23:59:59Z\n",
        200,
        {"Content-Type": "text/plain"},
    )


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "northstar-identity"})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if email == VIEWER_EMAIL and password == VIEWER_PASSWORD:
        return jsonify(
            {
                "ok": True,
                "account": {
                    "email": VIEWER_EMAIL,
                    "role": "security-analyst",
                },
            }
        )
    return jsonify({"ok": False, "error": "invalid credentials"}), 403


@app.route("/api/password-reset", methods=["POST"])
def password_reset():
    data = request.get_json(silent=True) or request.form
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "email is required"}), 400

    request_id = "reset-" + secrets.token_hex(6)
    message_id = None
    if email in {VIEWER_EMAIL, ADMIN_EMAIL}:
        messages = load_messages()
        message_id = f"msg-{len(messages) + 1:04d}"
        reset_token = "rst-" + secrets.token_hex(8)
        body_lines = [
            "A password reset was requested for your Northstar account.",
            "",
            f"Reset link token: {reset_token}",
        ]
        if email == ADMIN_EMAIL:
            body_lines.extend(
                [
                    "",
                    f"Break-glass recovery reference: {BREAK_GLASS_REFERENCE}",
                ]
            )
        body_lines.extend(["", "If you did not request this, contact Identity Operations."])
        messages.append(
            {
                "id": message_id,
                "to": email,
                "subject": "Northstar password reset",
                "body": "\n".join(body_lines),
                "created_at": int(time.time()),
            }
        )
        save_messages(messages)

    RESET_REQUESTS[request_id] = {
        "status": "sent",
        "message_id": message_id,
        "requested_at": int(time.time()),
    }
    return jsonify(
        {
            "ok": True,
            "message": "If the account exists, a reset message has been sent.",
            "request_id": request_id,
            "status_url": f"/api/password-reset/status/{request_id}",
        }
    )


@app.route("/api/password-reset/status/<request_id>")
def reset_status(request_id: str):
    record = RESET_REQUESTS.get(request_id)
    if record is None:
        return jsonify({"error": "reset request not found"}), 404

    response = {
        "request_id": request_id,
        "status": record["status"],
        "requested_at": record["requested_at"],
    }
    if record["message_id"]:
        response["delivery_debug_url"] = "http://mail.northstar.internal/api/messages/" + str(
            record["message_id"]
        )
    return jsonify(response)


if __name__ == "__main__":
    RESET_REQUESTS.clear()
    save_messages([])
    app.run(host="0.0.0.0", port=5000, debug=False)
