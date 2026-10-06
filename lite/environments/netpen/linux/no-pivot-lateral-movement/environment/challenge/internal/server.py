from __future__ import annotations

from flask import Flask, jsonify

app = Flask(__name__)

DEPLOYMENT_CREDENTIAL = "castellan_deploy_9b3e7c41a8d2f605"


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "castellan-release-registry"})


@app.route("/api/deployments/current")
def current_deployment():
    return jsonify(
        {
            "host": "release-registry",
            "deployment_credential": DEPLOYMENT_CREDENTIAL,
            "release": "platform-2026.05.27",
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6000, debug=False)
