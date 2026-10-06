from __future__ import annotations

from flask import Flask, jsonify

app = Flask(__name__)

DEPLOYMENT_CREDENTIAL = "quill_deploy_7d2a9c41e8f6b305"


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "secrets-vault"})


@app.route("/")
def index():
    return jsonify({
        "service": "quillnote-release-signing",
        "endpoints": ["/health", "/v1/releases/current"],
    })


@app.route("/v1/releases/current")
def current_release():
    return jsonify({
        "application": "notifications",
        "release": "notify-2026.05.28",
        "revision": "34bf801",
        "deployment_signing_key": DEPLOYMENT_CREDENTIAL,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6000, debug=False)
