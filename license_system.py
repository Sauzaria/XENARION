import hmac
import hashlib
import base64
import struct
import time
import os
import subprocess

# ============================================================
# 秘密鍵（絶対に配布しない！）
# ============================================================
SECRET_KEY = b"CHANGE-THIS-TO-YOUR-OWN-SECRET-KEY-XENARION-2025"

TIER_NAMES = {0: "BASIC", 1: "PRO", 2: "ULTIMATE"}

LICENSE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "license.txt")


# ============================================================
# HWID 取得
# ============================================================
def get_hwid():
    """この PC のユニークIDを取得"""
    try:
        out = subprocess.check_output(
            "wmic csproduct get uuid",
            shell=True, stderr=subprocess.DEVNULL
        ).decode(errors="ignore")
        lines = [l.strip() for l in out.splitlines() if l.strip() and "UUID" not in l]
        if lines and lines[0]:
            return lines[0]
    except Exception:
        pass
    try:
        import uuid
        return str(uuid.getnode())
    except Exception:
        return "UNKNOWN-HWID"


def _hash_hwid(hwid):
    if not hwid:
        return 0
    return int.from_bytes(hashlib.sha256(hwid.encode()).digest()[:2], "big")


def _hmac(payload):
    return hmac.new(SECRET_KEY, payload, hashlib.sha256).digest()[:6]


# ============================================================
# キー生成（販売者側）
# ============================================================
def generate_key(tier=1, days=30, hwid=""):
    """
    tier   : 0=BASIC, 1=PRO, 2=ULTIMATE
    days   : 有効日数。0 で無期限
    hwid   : 紐付けるHWID。空文字で全PC有効
    """
    now_days = int(time.time() // 86400)
    expiry_days = (now_days + days) if days > 0 else 0
    hwid_hash = _hash_hwid(hwid)
    # 8 bytes payload
    payload = struct.pack(">BBHHH", 1, tier, 0, expiry_days, hwid_hash)  # 1+1+2+2+2
    sig = _hmac(payload)
    combined = payload + sig  # 14 bytes
    b32 = base64.b32encode(combined).decode().rstrip("=")  # 24文字
    return "-".join(b32[i:i+4] for i in range(0, 24, 4))


# ============================================================
# キー検証（クライアント側）
# ============================================================
def verify_key(key, hwid=""):
    try:
        clean = key.replace("-", "").replace(" ", "").upper()
        pad = "=" * (-len(clean) % 8)
        combined = base64.b32decode(clean + pad)
        if len(combined) != 14:
            return {"valid": False, "reason": "invalid_length"}

        payload, sig = combined[:8], combined[8:]
        if not hmac.compare_digest(sig, _hmac(payload)):
            return {"valid": False, "reason": "invalid_signature"}

        ver, tier, flags, expiry_days, hwid_hash = struct.unpack(">BBHHH", payload)
        now_days = int(time.time() // 86400)

        if expiry_days and now_days > expiry_days:
            remaining = expiry_days - now_days
            return {"valid": False, "reason": f"expired ({-remaining} days ago)"}

        if hwid_hash:
            if _hash_hwid(hwid) != hwid_hash:
                return {"valid": False, "reason": "hwid_mismatch"}

        return {
            "valid": True,
            "tier": tier,
            "tier_name": TIER_NAMES.get(tier, "UNKNOWN"),
            "expiry_days": expiry_days,
            "remaining_days": (expiry_days - now_days) if expiry_days else None,
            "hwid_bound": hwid_hash != 0,
        }
    except Exception as e:
        return {"valid": False, "reason": f"parse_error: {e}"}


# ============================================================
# ライセンスファイル管理
# ============================================================
def save_license(key):
    with open(LICENSE_PATH, "w", encoding="utf-8") as f:
        f.write(key.strip())


def load_license():
    if os.path.exists(LICENSE_PATH):
        try:
            with open(LICENSE_PATH, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""


def check_saved_license():
    """保存済みライセンスを検証"""
    key = load_license()
    if not key:
        return {"valid": False, "reason": "no_license"}
    return verify_key(key, get_hwid())