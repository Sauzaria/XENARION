import os
import hashlib
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Flask, request, jsonify
from dotenv import load_dotenv

from models import db, LicenseKey

load_dotenv()

app = Flask(__name__)

# ============================================================
# 設定
# ============================================================
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///local.db")
# Render の DATABASE_URL は postgres:// 形式なので postgresql:// に変換
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret")

db.init_app(app)

# 管理用APIキー（クライアントには配布しない）
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "CHANGE_ME_ADMIN")


def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Admin-Key", "")
        if token != ADMIN_API_KEY:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


# ============================================================
# ヘルスチェック
# ============================================================
@app.route("/")
def index():
    return jsonify({
        "service": "XENARION LICENSE SERVER",
        "status": "online",
        "version": "1.0.0",
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


# ============================================================
# ライセンス検証（クライアントが呼ぶ）
# ============================================================
@app.route("/api/verify", methods=["POST"])
def api_verify():
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    hwid = (data.get("hwid") or "").strip()

    if not key:
        return jsonify({"valid": False, "reason": "missing_key"}), 400
    if not hwid:
        return jsonify({"valid": False, "reason": "missing_hwid"}), 400

    lic = LicenseKey.query.filter_by(key=key).first()
    if not lic:
        return jsonify({"valid": False, "reason": "not_found"}), 404

    if lic.revoked:
        return jsonify({"valid": False, "reason": "revoked"}), 403

    now = datetime.now(timezone.utc)

    if lic.expires_at:
        exp = lic.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if now > exp:
            return jsonify({
                "valid": False,
                "reason": "expired",
                "expired_at": exp.isoformat(),
            }), 403

    # HWID 紐付け
    if lic.hwid is None:
        lic.hwid = hwid
        db.session.commit()
    elif lic.hwid != hwid:
        return jsonify({"valid": False, "reason": "hwid_mismatch"}), 403

    # 最終確認時刻を更新
    lic.last_seen = now
    lic.activation_count = (lic.activation_count or 0) + 1
    db.session.commit()

    remaining = None
    if lic.expires_at:
        exp = lic.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        remaining = max(0, (exp - now).days)

    return jsonify({
        "valid": True,
        "tier": lic.tier,
        "tier_name": {0: "BASIC", 1: "PRO", 2: "ULTIMATE"}.get(lic.tier, "UNKNOWN"),
        "remaining_days": remaining,
        "server_time": now.isoformat(),
    })


# ============================================================
# 管理API（販売者専用）
# ============================================================
@app.route("/api/admin/create", methods=["POST"])
@require_admin
def api_admin_create():
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    if not key:
        return jsonify({"error": "missing_key"}), 400

    tier = int(data.get("tier", 1))
    hwid = data.get("hwid") or None
    days = int(data.get("days", 30))

    expires_at = None
    if days > 0:
        expires_at = datetime.now(timezone.utc) + timedelta(days=days)

    if LicenseKey.query.filter_by(key=key).first():
        return jsonify({"error": "key_exists"}), 409

    lic = LicenseKey(key=key, tier=tier, hwid=hwid, expires_at=expires_at)
    db.session.add(lic)
    db.session.commit()

    return jsonify({"created": True, "license": lic.to_dict()})


@app.route("/api/admin/revoke", methods=["POST"])
@require_admin
def api_admin_revoke():
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    lic = LicenseKey.query.filter_by(key=key).first()
    if not lic:
        return jsonify({"error": "not_found"}), 404
    lic.revoked = True
    db.session.commit()
    return jsonify({"revoked": True, "key": key})


@app.route("/api/admin/reset_hwid", methods=["POST"])
@require_admin
def api_admin_reset_hwid():
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    lic = LicenseKey.query.filter_by(key=key).first()
    if not lic:
        return jsonify({"error": "not_found"}), 404
    lic.hwid = None
    db.session.commit()
    return jsonify({"reset": True, "key": key})


@app.route("/api/admin/list", methods=["GET"])
@require_admin
def api_admin_list():
    licenses = LicenseKey.query.order_by(LicenseKey.created_at.desc()).all()
    return jsonify({"count": len(licenses),
                    "licenses": [l.to_dict() for l in licenses]})


# ============================================================
# 起動時テーブル作成
# ============================================================
with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)