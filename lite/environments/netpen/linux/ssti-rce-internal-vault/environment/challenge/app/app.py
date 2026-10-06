from __future__ import annotations

import os

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

SIGNING_SERVICE_URL = os.environ.get("RELEASE_SIGNING_SERVICE_URL", "")


@app.after_request
def add_headers(response):
    response.headers["X-Quillnote-Build"] = "notify-2026.05.28"
    return response


@app.route("/")
def index():
    return jsonify({
        "service": "quillnote-notifications",
        "host": "app",
        "endpoints": {
            "POST /api/templates/preview": (
                "Render a notification template. Body: {\"template\": \"...\"}"
            ),
            "GET /api/info": "Service and version info",
        },
    })


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "quillnote-notifications", "host": "app"})


@app.route("/api/info")
def info():
    return jsonify({
        "host": "app",
        "role": "Quillnote notification-template renderer",
        "version": "notify-2026.05.28",
        "release_signing": {
            "provider": "remote",
            "configured": bool(SIGNING_SERVICE_URL),
        },
    })


@app.route("/api/templates/preview", methods=["POST"])
def preview():
    data = request.get_json(silent=True) or {}
    template = data.get("template", "")
    if not template:
        return jsonify({"error": "template is required"}), 400
    try:
        rendered = render_template_string(template)
    except Exception as exc:  # noqa: BLE001 - surface render errors to the caller
        return jsonify({"error": f"render failed: {exc}"}), 400
    return jsonify({"rendered": rendered})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
