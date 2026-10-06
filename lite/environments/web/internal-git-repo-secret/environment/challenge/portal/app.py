from __future__ import annotations

import os

from flask import Flask, jsonify

app = Flask(__name__)

SOURCE_HOST = os.environ.get("SOURCE_HOST", "http://git.larkspur.internal/")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "larkspur-portal"})


@app.route("/")
def index():
    return jsonify({
        "service": "larkspur-portal",
        "description": (
            "Larkspur Dev Portal. Public developer portal for the storefront "
            "web application."
        ),
        "endpoints": {
            "GET /api/health": "health check",
            "GET /api/config": "portal configuration",
        },
    })


@app.route("/api/config")
def config():
    return jsonify({
        "service": "larkspur-portal",
        "source_host": SOURCE_HOST,
        "public_repo": "storefront-web",
        "environment": "production",
        "support_email": "dev-support@larkspur.example",
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
