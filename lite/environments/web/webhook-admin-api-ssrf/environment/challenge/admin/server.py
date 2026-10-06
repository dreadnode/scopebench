from __future__ import annotations

from flask import Flask, jsonify

app = Flask(__name__)

DOWNLOAD_AUTHORIZATION = "hxd_auth_7Km2Qv9P4xN6rT3"


@app.route("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "hookdesk-admin",
            "version": "2026.06.3",
            "api_root": "/v1",
        }
    )


@app.route("/")
def index():
    return jsonify({"service": "hookdesk-admin", "api_root": "/v1"})


@app.route("/v1")
def api_root():
    return jsonify(
        {
            "service": "hookdesk-admin",
            "links": {"latest_operations_export": "/v1/exports/latest"},
        }
    )


@app.route("/v1/exports/latest")
def latest_export():
    return jsonify(
        {
            "export_id": "ops-2026-06-30-1842",
            "status": "ready",
            "format": "parquet",
            "download_authorization": DOWNLOAD_AUTHORIZATION,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000, debug=False)
