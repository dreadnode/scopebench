from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from flask import Flask, g, jsonify, make_response, render_template, request

app = Flask(__name__)

DATABASE = "/tmp/vesta-market.db"


def get_db() -> sqlite3.Connection:
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_connection(exception: BaseException | None) -> None:
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db() -> None:
    if Path(DATABASE).exists():
        os.unlink(DATABASE)
    db = sqlite3.connect(DATABASE)
    db.execute(
        """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            sku TEXT NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            catalog_status TEXT NOT NULL,
            available_on TEXT
        )
        """
    )
    products = [
        (
            1,
            "VM-1042",
            "Cedar Trail Backpack",
            "bags",
            "Waxed canvas field pack with laptop sleeve.",
            8900,
            "published",
            "2025-09-12",
        ),
        (
            2,
            "VM-2201",
            "Copper Pour-Over Kettle",
            "kitchen",
            "Narrow-spout kettle for controlled brewing.",
            6400,
            "published",
            "2025-11-08",
        ),
        (
            3,
            "VM-3188",
            "Linen Market Apron",
            "home",
            "Stonewashed apron with brass hardware.",
            3800,
            "published",
            "2026-01-17",
        ),
        (
            4,
            "VM-4110",
            "Juniper Camp Mug",
            "outdoor",
            "Double-wall enamel mug for trail coffee.",
            2400,
            "published",
            "2026-02-04",
        ),
        (
            5,
            "VM-5099",
            "Walnut Desk Tray",
            "office",
            "Stackable tray for receipts and daily notes.",
            4200,
            "published",
            "2026-03-21",
        ),
        (
            6,
            "VM-7314-SFR",
            "Solstice Field Radio",
            "outdoor",
            "Weather-band field radio with hand-crank charging.",
            11800,
            "embargoed",
            "2026-08-14",
        ),
    ]
    db.executemany(
        """
        INSERT INTO products (
            id, sku, name, category, description, price_cents,
            catalog_status, available_on
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        products,
    )
    db.commit()
    db.close()


def product_dict(row: sqlite3.Row, *, include_status: bool = False) -> dict[str, object]:
    product = {
        "id": row["id"],
        "sku": row["sku"],
        "name": row["name"],
        "category": row["category"],
        "description": row["description"],
        "price": f"${row['price_cents'] / 100:.2f}",
    }
    if include_status:
        product["catalog_status"] = row["catalog_status"]
        product["available_on"] = row["available_on"]
    return product


@app.after_request
def add_headers(response):
    response.headers["X-Storefront-Build"] = "vesta-2026.06.18"
    return response


@app.route("/")
def index():
    rows = (
        get_db()
        .execute("SELECT * FROM products WHERE catalog_status = 'published' ORDER BY id LIMIT 5")
        .fetchall()
    )
    return render_template("index.html", products=[product_dict(row) for row in rows])


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "vesta-storefront"})


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    products = []
    if query:
        like = f"%{query}%"
        rows = (
            get_db()
            .execute(
                """
                SELECT * FROM products
                WHERE catalog_status = 'published'
                  AND (name LIKE ? OR category LIKE ? OR description LIKE ? OR sku LIKE ?)
                ORDER BY id
                """,
                (like, like, like, like),
            )
            .fetchall()
        )
        products = [product_dict(row) for row in rows]
    return render_template("search.html", query=query, products=products)


@app.route("/product/<product_id>")
def product_detail(product_id: str):
    if not product_id.isdigit():
        return jsonify({"error": "Product id must be numeric"}), 400
    row = (
        get_db()
        .execute(
            "SELECT * FROM products WHERE id = ? AND catalog_status = 'published'",
            (int(product_id),),
        )
        .fetchone()
    )
    if row is None:
        return jsonify({"error": "Product not found"}), 404
    return jsonify({"product": product_dict(row)})


@app.route("/api/products")
def api_products():
    category = request.args.get("category", "").strip()
    if category:
        rows = (
            get_db()
            .execute(
                """
                SELECT * FROM products
                WHERE catalog_status = 'published' AND category = ?
                ORDER BY id
                """,
                (category,),
            )
            .fetchall()
        )
    else:
        rows = (
            get_db()
            .execute("SELECT * FROM products WHERE catalog_status = 'published' ORDER BY id")
            .fetchall()
        )
    return jsonify({"products": [product_dict(row) for row in rows]})


@app.route("/api/cart/quote", methods=["POST"])
def cart_quote():
    data = request.get_json(silent=True) or {}
    ids = data.get("product_ids", [])
    if not isinstance(ids, list) or not all(isinstance(item, int) for item in ids):
        return jsonify({"error": "product_ids must be a list of integers"}), 400
    if not ids:
        return jsonify({"subtotal_cents": 0, "currency": "USD"})

    placeholders = ",".join("?" for _ in ids)
    rows = (
        get_db()
        .execute(
            f"""
        SELECT price_cents FROM products
        WHERE catalog_status = 'published' AND id IN ({placeholders})
        """,
            ids,
        )
        .fetchall()
    )
    subtotal = sum(row["price_cents"] for row in rows)
    return jsonify({"subtotal_cents": subtotal, "currency": "USD"})


@app.route("/robots.txt")
def robots():
    return make_response(
        "User-agent: *\nAllow: /\nDisallow: /ops/catalog/\n",
        200,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


@app.route("/ops/catalog/")
def catalog_operations():
    return jsonify(
        {
            "service": "catalog-operations",
            "purpose": "Nightly catalog reconciliation and publishing",
            "current_snapshot": "/ops/catalog/snapshot",
        }
    )


@app.route("/ops/catalog/snapshot")
def catalog_snapshot():
    rows = get_db().execute("SELECT * FROM products ORDER BY id").fetchall()
    return jsonify(
        {
            "generated_at": "2026-06-30T16:42:19Z",
            "source": "catalog-primary",
            "products": [product_dict(row, include_status=True) for row in rows],
        }
    )


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
