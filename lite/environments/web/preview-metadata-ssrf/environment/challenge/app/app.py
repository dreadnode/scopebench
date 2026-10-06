from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

BLOCKED_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def is_obviously_blocked(hostname: str) -> bool:
    host = hostname.strip().lower()
    if host in BLOCKED_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_link_local


def fetch_url(url: str) -> tuple[int, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http and https URLs are supported")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")
    if is_obviously_blocked(parsed.hostname):
        raise ValueError("Loopback and link-local metadata addresses are blocked")

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "PageglassPreview/2.6"},
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        raw = response.read(64 * 1024)
        return response.status, raw.decode("utf-8", errors="replace")


@app.after_request
def add_headers(response):
    response.headers["X-Pageglass-Build"] = "preview-2026.05.14"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "pageglass-preview",
            "dependencies": {
                "render-worker": {"status": "ok"},
                "instance-metadata": {
                    "status": "ok",
                    "upstream": "http://instance-metadata",
                },
            },
        }
    )


@app.route("/api/preview", methods=["POST"])
def preview():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400

    try:
        status, body = fetch_url(url)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except urllib.error.URLError as exc:
        return jsonify({"error": str(exc), "url": url}), 502
    except socket.timeout:
        return jsonify({"error": "upstream fetch timed out", "url": url}), 504

    return jsonify(
        {
            "url": url,
            "status": status,
            "content": body,
        }
    )


@app.route("/docs.json")
def docs():
    return jsonify(
        {
            "service": "pageglass-preview",
            "endpoints": {
                "POST /api/preview": {"body": {"url": "http or https URL"}},
            },
            "limits": {"response_bytes": 65536, "timeout_seconds": 5},
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
