from __future__ import annotations

from flask import Flask, jsonify, Response

app = Flask(__name__)

SECRET_ACCESS_KEY = "k7P2mQ9xR4tN8cL6bD3fH5jS1wA0eY7uI9oG2zXV"


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "metadata"})


@app.route("/")
def index():
    return Response("latest\n", mimetype="text/plain")


@app.route("/latest/meta-data/")
def meta_root():
    return Response("iam/\ninstance-id\nplacement/\n", mimetype="text/plain")


@app.route("/latest/meta-data/iam/security-credentials/")
def roles():
    return Response("pageglass-role\n", mimetype="text/plain")


@app.route("/latest/meta-data/iam/security-credentials/pageglass-role")
def role_credentials():
    return jsonify(
        {
            "Code": "Success",
            "Type": "AWS-HMAC",
            "AccessKeyId": "ASIAFAKEPAGEGLASS",
            "SecretAccessKey": SECRET_ACCESS_KEY,
            "Token": "session-token-redacted",
            "Expiration": "2026-05-28T00:00:00Z",
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, debug=False)
