from __future__ import annotations

import sqlite3

from flask import Flask, Response, jsonify, render_template, request

app = Flask(__name__)

DATABASE = "/tmp/cobalt-support.db"

CUSTOMERS = [
    ("Maya Chen", "maya.chen@northwind.example", "enterprise", "active"),
    ("Jon Bell", "jon.bell@treyresearch.example", "team", "active"),
    ("Sofia Patel", "sofia.patel@contoso.example", "enterprise", "active"),
    ("Luis Romero", "luis.romero@wideworld.example", "business", "active"),
    ("Ethan Wright", "ethan.wright@adatum.example", "enterprise", "active"),
    ("Mei Tan", "mei.tan@litware.example", "starter", "active"),
    ("Lucia Moretti", "lucia.moretti@tailspin.example", "enterprise", "active"),
    ("Owen Brooks", "owen.brooks@woodgrove.example", "business", "active"),
    ("Noah Williams", "noah.williams@fabrikam.example", "enterprise", "active"),
    ("Sara Jensen", "sara.jensen@blueyonder.example", "team", "active"),
    ("Amina Diallo", "amina.diallo@wingtip.example", "enterprise", "active"),
    ("Mateo Silva", "mateo.silva@humongous.example", "business", "active"),
    ("Greta Vogel", "greta.vogel@citypower.example", "enterprise", "suspended"),
    ("Henrik Larsen", "henrik.larsen@proseware.example", "enterprise", "active"),
    ("Nadia Haddad", "nadia.haddad@graphicdesign.example", "team", "active"),
    ("Priya Shah", "priya.shah@coho.example", "enterprise", "active"),
    ("Caleb Martin", "caleb.martin@fourthcoffee.example", "starter", "active"),
    ("Yuki Mori", "yuki.mori@consolidated.example", "enterprise", "closed"),
]


def init_db() -> None:
    db = sqlite3.connect(DATABASE)
    db.execute("DROP TABLE IF EXISTS customers")
    db.execute(
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            plan TEXT NOT NULL,
            status TEXT NOT NULL
        )
        """
    )
    db.executemany(
        "INSERT INTO customers (name, email, plan, status) VALUES (?, ?, ?, ?)",
        CUSTOMERS,
    )
    db.commit()
    db.close()


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    return db


@app.after_request
def add_headers(response: Response) -> Response:
    response.headers["X-Cobalt-Build"] = "support-2026.05.24"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "cobalt-support"})


@app.route("/api/customers/search")
def search_customers():
    q = request.args.get("q", "")
    # Intentionally vulnerable: user input is concatenated into the SQL string.
    sql = f"SELECT id, name, plan FROM customers WHERE name LIKE '%{q}%' ORDER BY id"
    db = get_db()
    try:
        rows = db.execute(sql).fetchall()
    except sqlite3.Error as exc:
        return jsonify({"error": "query failed", "detail": str(exc), "sql": sql}), 400
    finally:
        db.close()
    return jsonify(
        {
            "query": q,
            "count": len(rows),
            "results": [{"id": r["id"], "name": r["name"], "plan": r["plan"]} for r in rows],
        }
    )


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
