from __future__ import annotations

from flask import Flask, jsonify

app = Flask(__name__)

RELEASE_RECORD_URL = (
    "http://ops.larkfield.internal/api/releases/storefront/2026.05.28"
)


@app.route("/")
def index():
    return jsonify({
        "service": "larkfield-shop-api",
        "description": "Larkfield Retail customer-facing storefront API.",
        "endpoints": {
            "GET /api/health": "Service health check.",
            "GET /api/config": "Public customer-facing service configuration.",
            "GET /api/build": "Current application build metadata.",
        },
    })


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "larkfield-shop-api", "host": "customer"})


@app.route("/api/config")
def config():
    return jsonify({
        "service": "larkfield-shop-api",
        "environment": "production",
        "region": "eu-west",
        "currency": "EUR",
    })


@app.route("/api/build")
def build():
    return jsonify({
        "application": "storefront",
        "release": "2026.05.28",
        "revision": "c91e7a4",
        "built_at": "2026-05-28T16:42:11Z",
        "release_record": RELEASE_RECORD_URL,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
