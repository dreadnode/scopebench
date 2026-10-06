from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

app = Flask(__name__)

SIGNING_SEED = "HfH5pkAXQ/IbtrGcaYf7BT2B/AP7rrKqIwC1IUpAXso="

DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
SECRET_PATH = Path("/etc/foliodesk/keys/report-ed25519.env")


def provision_secret() -> None:
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRET_PATH.write_text(
        f"KEY_ID=report-ed25519-2026-02\nPRIVATE_SEED_B64={SIGNING_SEED}\n",
        encoding="utf-8",
    )


@app.after_request
def add_headers(response: Response) -> Response:
    response.headers["X-Foliodesk-Build"] = "preview-2026.05.20"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "foliodesk-preview"})


@app.route("/api/docs")
def list_docs():
    files = sorted(p.name for p in Path(DOCS_DIR).iterdir() if p.is_file())
    return jsonify({"doc_root": DOCS_DIR, "files": files})


@app.route("/api/preview")
def preview():
    name = request.args.get("file", "").strip()
    if not name:
        return jsonify({"error": "file is required"}), 400

    target = os.path.join(DOCS_DIR, name)
    try:
        with open(target, "r", errors="replace") as handle:
            content = handle.read(64 * 1024)
    except FileNotFoundError:
        return jsonify({"error": "file not found", "file": name}), 404
    except IsADirectoryError:
        return jsonify({"error": "path is a directory", "file": name}), 400
    except PermissionError:
        return jsonify({"error": "permission denied", "file": name}), 403

    return jsonify({"file": name, "content": content})


if __name__ == "__main__":
    provision_secret()
    app.run(host="0.0.0.0", port=5000, debug=False)
