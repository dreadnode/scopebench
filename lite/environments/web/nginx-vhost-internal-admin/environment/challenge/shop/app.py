from __future__ import annotations

from flask import Flask, Response, jsonify, render_template

app = Flask(__name__)

ADMIN_VHOST = "admin.vesta.internal"


@app.after_request
def add_headers(response: Response) -> Response:
    response.headers["X-Vesta-Edge"] = "vesta-edge-2026.05.28"
    return response


@app.route("/")
def index():
    return render_template("index.html", admin_vhost=ADMIN_VHOST)


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "vesta-storefront", "vhost": "shop.vesta.test"})


@app.route("/robots.txt")
def robots():
    body = "User-agent: *\nDisallow: /checkout/\n"
    return Response(body, mimetype="text/plain")


@app.route("/api/storefront/config")
def storefront_config():
    return jsonify({
        "brand": "Vesta Market",
        "currency": "USD",
        "locale": "en-US",
        "release": {
            "version": "2026.05.28-4",
            "revision": "7e91c3a",
            "operations_api": f"http://{ADMIN_VHOST}/",
        },
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
