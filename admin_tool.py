import os
import sys
import json
import secrets
import requests

SERVER = os.environ.get("XENARION_SERVER", "https://xenarion-license.onrender.com")
ADMIN_KEY = os.environ.get("XENARION_ADMIN_KEY", "")

# Base32風キー生成（読み間違い防止）
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

def gen_key():
    return "-".join(
        "".join(secrets.choice(ALPHABET) for _ in range(4))
        for _ in range(6)
    )


def create_key(tier=1, days=30, hwid=None):
    key = gen_key()
    r = requests.post(
        f"{SERVER}/api/admin/create",
        headers={"X-Admin-Key": ADMIN_KEY},
        json={"key": key, "tier": tier, "days": days, "hwid": hwid},
        timeout=30,
    )
    print(r.status_code, r.json())
    return key


def revoke_key(key):
    r = requests.post(
        f"{SERVER}/api/admin/revoke",
        headers={"X-Admin-Key": ADMIN_KEY},
        json={"key": key},
        timeout=30,
    )
    print(r.status_code, r.json())


def list_keys():
    r = requests.get(
        f"{SERVER}/api/admin/list",
        headers={"X-Admin-Key": ADMIN_KEY},
        timeout=30,
    )
    print(json.dumps(r.json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if not ADMIN_KEY:
        print("環境変数 XENARION_ADMIN_KEY を設定してください")
        sys.exit(1)
    print("1) キー生成  2) 失効  3) 一覧")
    c = input("> ").strip()
    if c == "1":
        tier = int(input("Tier (0/1/2) > ") or "1")
        days = int(input("Days (0=無期限) > ") or "30")
        hwid = input("HWID (空=全PC) > ").strip() or None
        print("KEY:", create_key(tier, days, hwid))
    elif c == "2":
        revoke_key(input("Key > ").strip())
    elif c == "3":
        list_keys()