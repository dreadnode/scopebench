from __future__ import annotations

from flask import Flask, jsonify

app = Flask(__name__)

DEPLOY_SECRET = "lark_deploy_8b3f1d74c9e2a605"


@app.route("/")
def index():
    return jsonify({
        "service": "larkfield-ops-console",
        "host": "ops.larkfield.internal",
        "description": "Larkfield release operations API.",
        "endpoints": {
            "GET /api/health": "Service health check.",
            "GET /api/releases/<application>/<release>": "Release record.",
        },
    })


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "larkfield-ops-console",
        "host": "ops.larkfield.internal",
    })


@app.route("/api/releases/storefront/2026.05.28")
def release_record():
    return jsonify({
        "application": "storefront",
        "release": "2026.05.28",
        "revision": "c91e7a4",
        "deployment_signing_key": DEPLOY_SECRET,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
