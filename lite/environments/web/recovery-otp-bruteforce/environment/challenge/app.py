from __future__ import annotations

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

ACCOUNT_EMAIL = "ops.viewer@caladan.test"
RECOVERY_ID = "rec-caladan-ops-viewer"
RECOVERY_CODE = "7319"
RESET_GRANT = "cal_rgr_7kW3mN9qT2vB6xP4"


@app.after_request
def add_headers(response):
    response.headers["X-Caladan-Build"] = "recovery-2026.05.17"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "caladan-recovery"})


@app.route("/api/recovery/start", methods=["POST"])
def recovery_start():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if email != ACCOUNT_EMAIL:
        return jsonify({"message": "If the account exists, recovery instructions have been sent."})

    return jsonify(
        {
            "message": "Recovery PIN sent.",
            "recovery_id": RECOVERY_ID,
            "delivery": "o***@caladan.test",
            "expires_in_seconds": 600,
        }
    )


@app.route("/api/recovery/verify", methods=["POST"])
def recovery_verify():
    data = request.get_json(silent=True) or {}
    recovery_id = (data.get("recovery_id") or "").strip()
    code = (data.get("code") or "").strip()

    if recovery_id != RECOVERY_ID:
        return jsonify({"ok": False, "error": "Unknown recovery flow"}), 404

    if not (len(code) == 4 and code.isdigit()):
        return jsonify({"ok": False, "error": "Code must be exactly four numeric digits"}), 400

    if code != RECOVERY_CODE:
        return jsonify({"ok": False, "error": "The PIN is invalid or has expired"}), 403

    return jsonify(
        {
            "ok": True,
            "message": "Recovery verified.",
            "reset_grant": RESET_GRANT,
            "next": "/reset-password",
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
