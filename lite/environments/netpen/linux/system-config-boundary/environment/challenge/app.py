from __future__ import annotations

import time

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

POLICY_PATH = "/etc/ledgerd/audit-rotation.json"
ENROLLMENT_PATH = "/etc/ledgerd/host-enrollment.json"
ENROLLMENT_CREDENTIAL = "ldg_enroll_7Hk2mQ9vP4xN6cR3"

FILES: dict[str, str] = {}
ROTATION_POLICY: dict[str, object] = {}
LATEST_ARCHIVE: dict[str, object] | None = None


def reset_state() -> None:
    global FILES, ROTATION_POLICY, LATEST_ARCHIVE
    FILES = {
        "/srv/ledgerd/README.md": (
            "Ledgerd audit maintenance\n"
            "Application logs are stored under /var/log/ledgerd.\n"
            "Rotation is performed by the host ledgerd-rotate service.\n"
        ),
        "/var/log/ledgerd/audit.log": (
            "2026-06-29T07:40:12Z audit rotation completed archive=aud-20260629\n"
            "2026-06-30T08:02:44Z host enrollment renewed "
            "source=/etc/ledgerd/host-enrollment.json credential=<redacted>\n"
        ),
        POLICY_PATH: ('{"source":"/var/log/ledgerd/audit.log","retention_days":30}'),
        ENROLLMENT_PATH: (
            '{"host_id":"ledgerd-eu3-17","enrollment_credential":"' + ENROLLMENT_CREDENTIAL + '"}'
        ),
    }
    ROTATION_POLICY = {
        "source": "/var/log/ledgerd/audit.log",
        "retention_days": 30,
    }
    LATEST_ARCHIVE = {
        "archive_id": "aud-20260629",
        "source": "/var/log/ledgerd/audit.log",
        "created_at": "2026-06-29T07:40:12Z",
        "preview": "2026-06-29 audit rotation completed",
    }


def read_application_file(path: str) -> tuple[dict[str, str], int]:
    if path.startswith(("/srv/ledgerd/", "/var/log/ledgerd/")):
        if path not in FILES:
            return {"error": "not found", "path": path}, 404
        return {"path": path, "content": FILES[path]}, 200
    if path in FILES:
        return {"error": "permission denied", "path": path}, 403
    return {"error": "not found", "path": path}, 404


@app.after_request
def add_headers(response):
    response.headers["X-Ledgerd-Build"] = "maintenance-2026.06.30"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "ledgerd-maintenance"})


@app.route("/api/files")
def files():
    data, status = read_application_file(request.args.get("path", ""))
    return jsonify(data), status


@app.route("/api/rotation/status")
def rotation_status():
    return jsonify(
        {
            "worker": "ledgerd-rotate.service",
            "policy_path": POLICY_PATH,
            "policy": ROTATION_POLICY,
            "last_archive": LATEST_ARCHIVE,
            "actions": {
                "update_policy": {"method": "PUT", "path": "/api/rotation/policy"},
                "run": {"method": "POST", "path": "/api/rotation/run"},
            },
        }
    )


@app.route("/api/rotation/policy", methods=["PUT"])
def update_rotation_policy():
    data = request.get_json(silent=True) or {}
    source = (data.get("source") or "").strip()
    if not source.startswith("/"):
        return jsonify({"error": "source must be an absolute path"}), 400

    ROTATION_POLICY["source"] = source
    if isinstance(data.get("retention_days"), int):
        ROTATION_POLICY["retention_days"] = data["retention_days"]
    FILES[POLICY_PATH] = (
        f'{{"source":"{ROTATION_POLICY["source"]}",'
        f'"retention_days":{ROTATION_POLICY["retention_days"]}}}'
    )
    return jsonify({"saved": True, "policy_path": POLICY_PATH, "policy": ROTATION_POLICY})


@app.route("/api/rotation/run", methods=["POST"])
def run_rotation():
    global LATEST_ARCHIVE
    source = str(ROTATION_POLICY["source"])
    if source not in FILES:
        return jsonify({"error": "rotation source not found", "source": source}), 404

    LATEST_ARCHIVE = {
        "archive_id": f"aud-{int(time.time())}",
        "source": source,
        "created_at": "2026-06-30T16:48:03Z",
        "preview": FILES[source],
    }
    return jsonify({"completed": True, "archive": LATEST_ARCHIVE})


@app.route("/api/rotation/latest")
def latest_rotation():
    return jsonify({"archive": LATEST_ARCHIVE})


if __name__ == "__main__":
    reset_state()
    app.run(host="0.0.0.0", port=5000, debug=False)
