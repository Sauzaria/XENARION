import os
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Flask, request, jsonify, render_template_string
from dotenv import load_dotenv

from models import db, LicenseKey

load_dotenv()

app = Flask(__name__)

# ============================================================
# 設定
# ============================================================
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///local.db")

# 接続URLの変換
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret")

db.init_app(app)

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
# 管理画面用のHTML (UI)
# ============================================================
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>XENARION // ADMIN</title>
    <style>
        :root {
            --bg: #050505; --panel: #0a0a0a; --border: #2a2a2a; --text: #e0e0e0; --accent: #fff;
        }
        body {
            background: var(--bg); color: var(--text); font-family: 'Consolas', 'Yu Gothic UI', sans-serif;
            margin: 0; padding: 20px; display: flex; flex-direction: column; align-items: center;
        }
        h1 { font-size: 20px; letter-spacing: 4px; color: #fff; margin-bottom: 5px; }
        .sub { font-size: 10px; letter-spacing: 2px; color: #666; margin-bottom: 30px; }
        .panel {
            background: var(--panel); border: 1px solid var(--border);
            padding: 20px; width: 100%; max-width: 900px; margin-bottom: 20px;
        }
        .form-group { display: flex; gap: 10px; margin-bottom: 15px; flex-wrap: wrap; }
        input, select {
            background: #000; color: #fff; border: 1px solid var(--border); padding: 8px 12px;
            font-family: inherit; outline: none; flex: 1; min-width: 150px;
        }
        input:focus, select:focus { border-color: var(--accent); }
        button {
            background: var(--accent); color: #000; border: none; padding: 8px 16px;
            font-weight: bold; letter-spacing: 1px; cursor: pointer; transition: 0.2s;
        }
        button:hover { background: #ccc; }
        button.danger { background: #ff4444; color: #fff; }
        button.danger:hover { background: #cc0000; }
        button.outline { background: transparent; border: 1px solid var(--border); color: var(--text); }
        button.outline:hover { border-color: var(--accent); color: #fff; }
        
        table { width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 12px; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid var(--border); }
        th { color: #888; font-weight: normal; letter-spacing: 1px; }
        .key-text { color: #fff; font-weight: bold; letter-spacing: 1px; }
        .badge { padding: 2px 6px; border-radius: 3px; font-size: 10px; border: 1px solid #555; }
        .badge.active { border-color: #4CAF50; color: #4CAF50; }
        .badge.revoked { border-color: #ff4444; color: #ff4444; }
        .badge.expired { border-color: #ff9800; color: #ff9800; }
        
        #login-section { display: block; }
        #dashboard-section { display: none; }
        #msg { margin-top: 10px; font-size: 12px; color: #4CAF50; display: none; }
    </style>
</head>
<body>

    <h1>▓▓▓ XENARION ADMIN</h1>
    <div class="sub">LICENSE MANAGEMENT SYSTEM</div>

    <!-- ログイン -->
    <div id="login-section" class="panel">
        <h3>■ 管理者ログイン</h3>
        <div class="form-group">
            <input type="password" id="api-key" placeholder="ADMIN_API_KEY を入力">
            <button onclick="login()">認証</button>
        </div>
    </div>

    <!-- ダッシュボード -->
    <div id="dashboard-section">
        <!-- キー発行 -->
        <div class="panel">
            <h3>■ 新規ライセンスキー発行</h3>
            <div class="form-group">
                <select id="tier">
                    <option value="0">BASIC</option>
                    <option value="1" selected>PRO</option>
                    <option value="2">ULTIMATE</option>
                </select>
                <input type="number" id="days" value="30" placeholder="有効日数 (0=無期限)">
                <input type="text" id="hwid" placeholder="HWID (空=全PC)">
                <button onclick="createKey()">発行</button>
            </div>
            <div id="msg"></div>
        </div>

        <!-- キー一覧 -->
        <div class="panel">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <h3>■ 発行済みキー一覧</h3>
                <button class="outline" onclick="loadKeys()">更新</button>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>KEY</th>
                        <th>TIER</th>
                        <th>期限</th>
                        <th>HWID</th>
                        <th>STATUS</th>
                        <th>操作</th>
                    </tr>
                </thead>
                <tbody id="key-list">
                    <tr><td colspan="6">読み込み中...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        let ADMIN_KEY = "";

        function showMsg(text, color = "#4CAF50") {
            const el = document.getElementById("msg");
            el.innerText = text;
            el.style.color = color;
            el.style.display = "block";
            setTimeout(() => { el.style.display = "none"; }, 3000);
        }

        function login() {
            const key = document.getElementById("api-key").value.trim();
            if (!key) return;
            ADMIN_KEY = key;
            
            // テスト用にAPIを叩く
            fetch('/api/admin/list', { headers: { 'X-Admin-Key': ADMIN_KEY } })
                .then(res => {
                    if (res.ok) {
                        document.getElementById("login-section").style.display = "none";
                        document.getElementById("dashboard-section").style.display = "block";
                        loadKeys();
                    } else {
                        alert("認証に失敗しました。APIキーが違います。");
                    }
                })
                .catch(() => alert("通信エラーが発生しました。"));
        }

        function createKey() {
            const tier = document.getElementById("tier").value;
            const days = document.getElementById("days").value;
            const hwid = document.getElementById("hwid").value.trim();
            
            // ランダムなキーを生成 (Base32形式)
            const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
            let key = "";
            for(let i=0; i<6; i++) {
                let block = "";
                for(let j=0; j<4; j++) block += chars.charAt(Math.floor(Math.random() * chars.length));
                key += block + (i < 5 ? "-" : "");
            }

            fetch('/api/admin/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Admin-Key': ADMIN_KEY },
                body: JSON.stringify({ key, tier: parseInt(tier), days: parseInt(days), hwid: hwid || null })
            })
            .then(res => res.json())
            .then(data => {
                if (data.created) {
                    showMsg("キーを発行しました: " + key);
                    loadKeys();
                } else {
                    showMsg("エラー: " + data.error, "#ff4444");
                }
            });
        }

        function loadKeys() {
            fetch('/api/admin/list', { headers: { 'X-Admin-Key': ADMIN_KEY } })
            .then(res => res.json())
            .then(data => {
                const tbody = document.getElementById("key-list");
                tbody.innerHTML = "";
                if (data.count === 0) {
                    tbody.innerHTML = "<tr><td colspan='6'>データがありません</td></tr>";
                    return;
                }
                data.licenses.forEach(lic => {
                    let statusHtml = "";
                    if (lic.revoked) {
                        statusHtml = "<span class='badge revoked'>REVOKED</span>";
                    } else if (lic.expires_at) {
                        const exp = new Date(lic.expires_at);
                        if (exp < new Date()) {
                            statusHtml = "<span class='badge expired'>EXPIRED</span>";
                        } else {
                            statusHtml = "<span class='badge active'>ACTIVE</span>";
                        }
                    } else {
                        statusHtml = "<span class='badge active'>UNLIMITED</span>";
                    }

                    const expText = lic.expires_at ? lic.expires_at.split('T')[0] : "無期限";
                    const hwidText = lic.hwid_bound ? "紐付け済" : "全PC";
                    
                    const tr = document.createElement("tr");
                    tr.innerHTML = `
                        <td class="key-text">${lic.key}</td>
                        <td>${lic.tier === 0 ? 'BASIC' : lic.tier === 1 ? 'PRO' : 'ULTIMATE'}</td>
                        <td>${expText}</td>
                        <td>${hwidText}</td>
                        <td>${statusHtml}</td>
                        <td>
                            <button class="danger" onclick="revokeKey('${lic.key}')" ${lic.revoked ? 'disabled' : ''}>失効</button>
                            <button class="outline" onclick="resetHWID('${lic.key}')" style="font-size:10px; padding:4px 8px;">HWID解除</button>
                        </td>
                    `;
                    tbody.appendChild(tr);
                });
            });
        }

        function revokeKey(key) {
            if (!confirm(key + " を失効させますか？")) return;
            fetch('/api/admin/revoke', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Admin-Key': ADMIN_KEY },
                body: JSON.stringify({ key })
            }).then(() => { loadKeys(); });
        }

        function resetHWID(key) {
            if (!confirm(key + " のHWID紐付けを解除しますか？")) return;
            fetch('/api/admin/reset_hwid', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Admin-Key': ADMIN_KEY },
                body: JSON.stringify({ key })
            }).then(() => { loadKeys(); });
        }
    </script>
</body>
</html>
"""


# ============================================================
# ルーティング
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


# ★ 管理画面の表示
@app.route("/admin")
def admin_ui():
    return render_template_string(ADMIN_HTML)


# ============================================================
# API (クライアント用)
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
# 管理用API
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