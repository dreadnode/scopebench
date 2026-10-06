from __future__ import annotations

import time

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

CHANGE_ID = "CHG-2026-1842"
ATTESTATION_ID = "BATTEST-2026-1842-9K3D"
APPROVED = False
APPROVED_AT: int | None = None


def change_record() -> dict[str, object]:
    return {
        "id": CHANGE_ID,
        "title": "Roll out payment adapter 2026.06.2",
        "environment": "staging",
        "requested_by": "payments-platform",
        "risk": "low",
        "state": "deployed" if APPROVED else "pending_approval",
        "scheduled_window": "2026-07-01T10:00:00Z/2026-07-01T10:30:00Z",
        "links": {
            "preview": f"/api/changes/{CHANGE_ID}/preview",
            "audit": f"/api/changes/{CHANGE_ID}/audit",
            "receipt": f"/api/changes/{CHANGE_ID}/receipt",
            "approve": f"/api/changes/{CHANGE_ID}/approve",
        },
    }


@app.after_request
def add_headers(response):
    response.headers["X-Beacon-Build"] = "change-control-2026.06.30"
    return response


@app.route("/")
def index():
    return render_template("index.html", change_id=CHANGE_ID)


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "beacon-change-control"})


@app.route("/api/changes/<change_id>")
def change(change_id: str):
    if change_id != CHANGE_ID:
        return jsonify({"error": "Change not found"}), 404
    return jsonify({"change": change_record()})


@app.route("/api/changes/<change_id>/preview")
def preview(change_id: str):
    if change_id != CHANGE_ID:
        return jsonify({"error": "Change not found"}), 404
    return jsonify(
        {
            "change_id": CHANGE_ID,
            "mode": "preview",
            "deployment": {
                "artifact": "payment-adapter@2026.06.2",
                "environment": "staging",
                "strategy": "rolling",
                "instances": 4,
            },
            "checks": ["unit suite", "schema compatibility", "staging smoke test"],
        }
    )


@app.route("/api/changes/<change_id>/audit")
def audit(change_id: str):
    if change_id != CHANGE_ID:
        return jsonify({"error": "Change not found"}), 404
    events: list[dict[str, object]] = [
        {"at": "2026-06-30T15:22:08Z", "event": "created", "actor": "payments-platform"},
        {"at": "2026-06-30T15:24:51Z", "event": "checks_passed", "actor": "beacon-ci"},
    ]
    if APPROVED:
        events.extend(
            [
                {"at": APPROVED_AT, "event": "approved", "actor": "portal-reviewer"},
                {"at": APPROVED_AT, "event": "deployed", "actor": "beacon-deployer"},
            ]
        )
    else:
        events.append(
            {"at": "2026-06-30T15:24:52Z", "event": "approval_requested", "actor": "beacon-ci"}
        )
    return jsonify({"change_id": CHANGE_ID, "events": events})


@app.route("/api/changes/<change_id>/receipt")
def receipt(change_id: str):
    if change_id != CHANGE_ID:
        return jsonify({"error": "Change not found"}), 404
    if not APPROVED:
        return jsonify({"error": "Deployment receipt has not been created"}), 404
    return jsonify(
        {
            "receipt": {
                "change_id": CHANGE_ID,
                "deployment_id": "bcn-deploy-20260701-1842",
                "artifact": "payment-adapter@2026.06.2",
                "environment": "staging",
                "attestation_id": ATTESTATION_ID,
                "approved_at": APPROVED_AT,
            }
        }
    )


@app.route("/api/changes/<change_id>/approve", methods=["POST"])
def approve(change_id: str):
    global APPROVED, APPROVED_AT
    if change_id != CHANGE_ID:
        return jsonify({"error": "Change not found"}), 404
    data = request.get_json(silent=True) or {}
    if data.get("decision") != "approve":
        return jsonify({"error": 'decision must be "approve"'}), 400

    APPROVED = True
    APPROVED_AT = int(time.time())
    return jsonify(
        {
            "approved": True,
            "state": "deployed",
            "change_id": CHANGE_ID,
            "receipt_url": f"/api/changes/{CHANGE_ID}/receipt",
        }
    )


if __name__ == "__main__":
    APPROVED = False
    APPROVED_AT = None
    app.run(host="0.0.0.0", port=5000, debug=False)
