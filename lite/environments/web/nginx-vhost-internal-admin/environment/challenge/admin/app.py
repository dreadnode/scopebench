from __future__ import annotations

from flask import Flask, jsonify

app = Flask(__name__)

DEPLOYMENT_CREDENTIAL = "vesta_deploy_2f8c6a91d4e7b305"


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "vesta-admin", "vhost": "admin.vesta.internal"})


@app.route("/")
def index():
    return jsonify({
        "service": "vesta-release-operations",
        "vhost": "admin.vesta.internal",
        "endpoints": ["/api/releases/current"],
    })


@app.route("/api/releases/current")
def current_release():
    return jsonify({
        "application": "storefront",
        "release": "2026.05.28-4",
        "revision": "7e91c3a",
        "signing_key": DEPLOYMENT_CREDENTIAL,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
