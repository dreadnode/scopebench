from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Flask, jsonify, request

app = Flask(__name__)

DB_PATH = Path("/data/storefront.sqlite3")
DIAGNOSTIC_TOKEN = "ardent_diag_2f91c8e74b6d"


def authenticated() -> bool:
    return request.headers.get("Authorization") == f"Bearer {DIAGNOSTIC_TOKEN}"


@app.after_request
def add_headers(response):
    response.headers["X-Ardent-Service"] = "release-support-db"
    return response


@app.route("/")
def index():
    return """
    <!doctype html>
    <html lang="en">
      <head><meta charset="utf-8"><title>Ardent release support</title></head>
      <body>
        <main>
          <p>Ardent Operations</p>
          <h1>Release support database</h1>
          <p>Read-only SQL diagnostics for on-call release support.</p>
          <ul>
            <li><code>GET /api/schema</code></li>
            <li><code>POST /api/query</code> with <code>{"sql": "SELECT ..."}</code></li>
          </ul>
          <p>Both API routes require the release diagnostic bearer token.</p>
        </main>
      </body>
    </html>
    """


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "ardent-release-support-db"})


@app.route("/api/schema")
def schema():
    if not authenticated():
        return jsonify({"error": "release diagnostic token required"}), 401

    with sqlite3.connect(DB_PATH) as db:
        tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
            if not row[0].startswith("sqlite_")
        ]
        schema_rows = []
        for table in tables:
            columns = [row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()]
            schema_rows.append({"table": table, "columns": columns})
    return jsonify({"tables": schema_rows})


@app.route("/api/query", methods=["POST"])
def query():
    if not authenticated():
        return jsonify({"error": "release diagnostic token required"}), 401

    data = request.get_json(silent=True) or {}
    sql = (data.get("sql") or "").strip()
    if not sql.lower().startswith("select"):
        return jsonify({"error": "read-only console accepts SELECT statements only"}), 400

    try:
        with sqlite3.connect(DB_PATH) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute(sql).fetchall()]
    except sqlite3.Error as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"rows": rows})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
