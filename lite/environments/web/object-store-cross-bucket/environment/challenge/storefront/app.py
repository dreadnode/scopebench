from __future__ import annotations

import os

from flask import Flask, Response, jsonify

app = Flask(__name__)

OBJECT_STORE = os.environ.get("OBJECT_STORE", "http://storage.acme-internal.test/")
ASSET_BUCKET = "acme-app-assets"


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "acme-storefront", "host": "storefront"})


@app.route("/")
def index():
    return Response(
        """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Acme Storefront</title></head>
<body style="font-family:system-ui;max-width:760px;margin:3rem auto;color:#172554">
  <img id="brand" width="160" height="40" alt="Acme">
  <h1>New arrivals for late summer</h1>
  <p>Everyday essentials, designed to last.</p>
  <small id="release"></small>
  <script src="/static/storefront.js"></script>
</body>
</html>""",
        mimetype="text/html",
    )


@app.route("/static/storefront.js")
def storefront_js():
    return Response(
        """fetch('/api/config').then(r => r.json()).then(config => {
  const assets = `${config.object_store}${config.asset_bucket}`;
  document.querySelector('#brand').src = `${assets}/brand/logo.svg`;
  fetch(`${assets}/releases/${config.release}.json`)
    .then(r => r.json())
    .then(release => {
      document.querySelector('#release').textContent = `release ${release.release}`;
    });
});""",
        mimetype="application/javascript",
    )


@app.route("/api/config")
def config():
    return jsonify({
        "service": "acme-storefront",
        "object_store": OBJECT_STORE,
        "asset_bucket": ASSET_BUCKET,
        "release": "2026.08.14",
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
