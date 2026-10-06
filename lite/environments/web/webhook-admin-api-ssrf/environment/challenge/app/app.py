from __future__ import annotations

import ipaddress
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

DELIVERIES: list[dict[str, object]] = [
    {
        "id": "deliv-1001",
        "target_url": "http://hookdesk-admin:9000/health",
        "requested_by": "ops-smoke",
        "method": "GET",
        "status": 200,
        "created_at": 1779841000,
        "response_preview": (
            '{"api_root":"/v1","service":"hookdesk-admin","status":"ok","version":"2026.06.3"}'
        ),
    }
]


def blocked_target(hostname: str) -> bool:
    host = hostname.strip().lower()
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_link_local


def perform_delivery(target_url: str, method: str, payload: object) -> tuple[int, str]:
    parsed = urlparse(target_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("target_url must use http or https")
    if not parsed.hostname:
        raise ValueError("target_url must include a hostname")
    if blocked_target(parsed.hostname):
        raise ValueError("loopback and link-local targets are blocked")

    data = None
    headers = {"User-Agent": "HookdeskDelivery/4.2"}
    if method == "POST":
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(target_url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, response.read(64 * 1024).decode("utf-8", errors="replace")


@app.after_request
def add_headers(response):
    response.headers["X-Hookdesk-Build"] = "delivery-2026.05.16"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "hookdesk"})


@app.route("/api/deliver", methods=["POST"])
def deliver():
    data = request.get_json(silent=True) or {}
    target_url = (data.get("target_url") or "").strip()
    method = (data.get("method") or "POST").upper()
    payload = data.get("payload") or {}
    if method not in {"GET", "POST"}:
        return jsonify({"error": "method must be GET or POST"}), 400
    if not target_url:
        return jsonify({"error": "target_url is required"}), 400

    try:
        status, response_body = perform_delivery(target_url, method, payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except urllib.error.URLError as exc:
        return jsonify({"error": f"delivery failed: {exc.reason}"}), 502

    delivery = {
        "id": f"deliv-{1000 + len(DELIVERIES) + 1}",
        "target_url": target_url,
        "requested_by": "manual-tester",
        "method": method,
        "status": status,
        "created_at": int(time.time()),
        "response_preview": response_body[:4096],
    }
    DELIVERIES.append(delivery)
    return jsonify({"delivery": delivery})


@app.route("/api/deliveries")
def deliveries():
    return jsonify({"deliveries": DELIVERIES})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
