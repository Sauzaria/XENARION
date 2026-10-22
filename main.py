import os
import sys
import math
import time
import json
import random
import threading
import ctypes
import urllib.request
import urllib.error
from ctypes import wintypes
from datetime import datetime

os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"

import cv2
import numpy as np
import onnxruntime as ort
import dxcam
import win32api
import win32con
import win32gui

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QGroupBox, QGridLayout,
    QSizePolicy, QFrame, QGraphicsDropShadowEffect, QStackedWidget,
    QScrollArea, QButtonGroup, QAbstractButton, QDialog, QLineEdit,
    QMessageBox
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QPropertyAnimation, QEasingCurve,
    pyqtProperty, QRectF, QPointF, QSize
)
from PyQt6.QtGui import (
    QImage, QPixmap, QFontDatabase, QColor, QPainter, QPen, QBrush,
    QLinearGradient, QFont, QPainterPath, QRadialGradient, QFontMetrics
)


# ============================================================
# サーバー設定（ここを自分のRender URLに変更）
# ============================================================
LICENSE_SERVER = os.environ.get(
    "XENARION_SERVER", "https://xenarion-license.onrender.com"
)
LICENSE_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "license_cache.json"
)
OFFLINE_GRACE_DAYS = 7


# ============================================================
# タイマー精度
# ============================================================
try:
    _winmm = ctypes.WinDLL("winmm")
    _winmm.timeBeginPeriod(1)
    _timer_res_raised = True
except Exception:
    _timer_res_raised = False


def precise_sleep(duration):
    if duration > 0:
        time.sleep(duration)


CAPTURE_TARGET_FPS = 240


# ============================================================
# SendInput
# ============================================================
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("i",)
    _fields_ = [("type", wintypes.DWORD), ("i", _I)]


INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001


def send_mouse_move(dx, dy):
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.i.mi.dx = int(dx)
    inp.i.mi.dy = int(dy)
    inp.i.mi.dwFlags = MOUSEEVENTF_MOVE
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


# ============================================================
# キー名マッピング
# ============================================================
KEY_NAMES = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter",
    0x10: "Shift", 0x11: "Ctrl", 0x12: "Alt", 0x1B: "Esc",
    0x14: "CapsLock", 0x20: "Space",
    0x21: "PageUp", 0x22: "PageDown", 0x23: "End", 0x24: "Home",
    0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓",
    0x2C: "PrintScreen", 0x2D: "Insert", 0x2E: "Delete",
    0x5B: "Win", 0x5C: "Win", 0x5D: "Menu",
    0x90: "NumLock", 0x91: "ScrollLock",
    0xA0: "LShift", 0xA1: "RShift",
    0xA2: "LCtrl", 0xA3: "RCtrl",
    0xA4: "LAlt", 0xA5: "RAlt",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}
for _c in range(0x30, 0x3A):
    KEY_NAMES[_c] = chr(_c)
for _c in range(0x41, 0x5B):
    KEY_NAMES[_c] = chr(_c)
for _c in range(0x70, 0x88):
    KEY_NAMES[_c] = f"F{_c - 0x6F}"
for _c in range(0x60, 0x6A):
    KEY_NAMES[_c] = f"Num{_c - 0x60}"
KEY_NAMES.update({0x6A: "Num*", 0x6B: "Num+", 0x6D: "Num-", 0x6E: "Num.", 0x6F: "Num/"})

KEY_MOUSE_LANG = {
    0x01: {"en": "Left Click", "jp": "左クリック", "kr": "좌클릭"},
    0x02: {"en": "Right Click", "jp": "右クリック", "kr": "우클릭"},
    0x04: {"en": "Middle Click", "jp": "中クリック", "kr": "휠클릭"},
    0x05: {"en": "Side 1", "jp": "サイド1", "kr": "사이드1"},
    0x06: {"en": "Side 2", "jp": "サイド2", "kr": "사이드2"},
}


def get_key_name(vk):
    if vk in KEY_MOUSE_LANG:
        lang = getattr(config, "language", "jp")
        return KEY_MOUSE_LANG[vk].get(lang, KEY_MOUSE_LANG[vk]["en"])
    if vk in KEY_NAMES:
        return KEY_NAMES[vk]
    return f"VK 0x{vk:02X}"


def snapshot_pressed_keys():
    pressed = set()
    for vk in range(1, 256):
        if win32api.GetAsyncKeyState(vk) & 0x8000:
            pressed.add(vk)
    return pressed


# ============================================================
# 設定
# ============================================================
class Config:
    def __init__(self):
        self.capture_size = 320
        self.fov_radius = 160
        self.conf_threshold = 0.45
        self.nms_threshold = 0.45
        self.head_offset_y = 0.0
        self.track_strength = 0.35
        self.track_x = 1.0
        self.track_y = 1.0
        self.smoothing = 0.0
        self.deadzone_px = 3.0
        self.recoil_compensation = 0.0
        self.lock_frames = 10
        self.lock_iou_threshold = 0.3
        self.fov_filter_enabled = True
        self.polling_rate = 1000
        self.activation_mode = "hold"
        self.aim_key = 0x02
        self.show_debug = True
        self.enabled = True
        self.language = "jp"
        self.fps_limit = 0
        self.active_profile = ""

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    def apply_dict(self, data):
        for k, v in data.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def save(self, path=None):
        if path is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=4, ensure_ascii=False)

    def load(self, path=None):
        if path is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.apply_dict(data)
            except Exception as e:
                print(f"[WARN] Config load error: {e}")


config = Config()
config.load()


# ============================================================
# HWID
# ============================================================
def get_hwid():
    import subprocess, uuid
    try:
        out = subprocess.check_output("wmic csproduct get uuid", shell=True,
                                      stderr=subprocess.DEVNULL).decode(errors="ignore")
        lines = [l.strip() for l in out.splitlines() if l.strip() and "UUID" not in l]
        if lines and lines[0]:
            return lines[0]
    except Exception:
        pass
    try:
        return str(uuid.getnode())
    except Exception:
        return "UNKNOWN-HWID"


# ============================================================
# ライセンス検証（サーバー + オフラインキャッシュ）
# ============================================================
def _load_cache():
    if os.path.exists(LICENSE_CACHE_PATH):
        try:
            with open(LICENSE_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cache(data):
    try:
        with open(LICENSE_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[WARN] cache save: {e}")


def verify_with_server(key, hwid, timeout=8):
    payload = json.dumps({"key": key, "hwid": hwid}).encode("utf-8")
    req = urllib.request.Request(
        f"{LICENSE_SERVER}/api/verify",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"valid": False, "reason": f"http_{e.code}"}
        return body
    except Exception as e:
        return {"valid": False, "reason": f"network_error: {e}"}


def validate_license(key):
    hwid = get_hwid()
    cache = _load_cache()
    now = time.time()

    # サーバー問い合わせ
    result = verify_with_server(key, hwid)
    if result.get("valid"):
        _save_cache({
            "key": key,
            "hwid": hwid,
            "tier": result.get("tier"),
            "tier_name": result.get("tier_name"),
            "remaining_days": result.get("remaining_days"),
            "last_ok": now,
        })
        return result

    reason = result.get("reason", "")

    # ネットワークエラー時のみオフライン猶予
    if "network_error" in reason or "http_5" in reason:
        if (cache.get("key") == key
                and cache.get("hwid") == hwid
                and (now - cache.get("last_ok", 0)) < OFFLINE_GRACE_DAYS * 86400):
            days_left = int((OFFLINE_GRACE_DAYS * 86400 - (now - cache["last_ok"])) // 86400)
            return {
                "valid": True,
                "tier_name": cache.get("tier_name", "PRO"),
                "remaining_days": cache.get("remaining_days"),
                "offline": True,
                "offline_grace_days": days_left,
            }
        return {"valid": False, "reason": "offline_no_cache"}

    # 明確な拒否（期限切れ・HWID不一致など）
    return result


# ============================================================
# ライセンス入力ダイアログ
# ============================================================
class LicenseDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("XENARION // LICENSE ACTIVATION")
        self.setFixedSize(560, 340)
        self.setStyleSheet("""
            QDialog { background: #050505; }
            QLabel { color: #dcdcdc; font-size: 12px; letter-spacing: 1px; }
            QLineEdit {
                background: #0a0a0a; color: #ffffff;
                border: 1px solid #3a3a3a; padding: 10px;
                font-family: Consolas; font-size: 14px; letter-spacing: 2px;
            }
            QLineEdit:focus { border: 1px solid #ffffff; }
            QPushButton {
                background: #0a0a0a; color: #ffffff;
                border: 1px solid #3a3a3a; padding: 10px 20px;
                font-weight: bold; letter-spacing: 2px;
            }
            QPushButton:hover { background: #ffffff; color: #000000; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(14)

        title = QLabel("▰▰  XENARION AI  //  LICENSE")
        title.setStyleSheet(
            "color: #ffffff; font-size: 18px; font-weight: bold; letter-spacing: 4px;")
        layout.addWidget(title)

        hwid = get_hwid()
        layout.addWidget(QLabel(f"HWID: {hwid}"))
        layout.addWidget(QLabel("ライセンスキーを入力してください:"))

        self.entry = QLineEdit()
        self.entry.setPlaceholderText("XXXX-XXXX-XXXX-XXXX-XXXX-XXXX")
        cache = _load_cache()
        if cache.get("key"):
            self.entry.setText(cache["key"])
        layout.addWidget(self.entry)

        self.msg = QLabel("")
        self.msg.setStyleSheet("color: #ff6666; font-size: 11px;")
        layout.addWidget(self.msg)

        self.btn = QPushButton("ACTIVATE // 認証")
        layout.addWidget(self.btn)
        self.btn.clicked.connect(self._activate)
        self.entry.returnPressed.connect(self._activate)

    def _activate(self):
        key = self.entry.text().strip()
        if not key:
            self.msg.setText("キーが空です")
            return

        self.msg.setStyleSheet("color: #dcdcdc; font-size: 11px;")
        self.msg.setText("サーバーに問い合わせ中...")
        QApplication.processEvents()

        result = validate_license(key)
        if not result.get("valid"):
            self.msg.setStyleSheet("color: #ff6666; font-size: 11px;")
            self.msg.setText(f"✗ {result.get('reason', 'unknown')}")
            return

        # キーを保存（キャッシュに保存済みだが、明示的に）
        tier = result.get("tier_name", "?")
        rem = result.get("remaining_days")
        QMessageBox.information(
            self, "認証成功",
            f"プラン: {tier}\n"
            + (f"残り: {rem}日" if rem is not None else "期間: 無期限")
        )
        self.accept()


# ============================================================
# モデル/推論/エイム（前回と同じ）
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(BASE_DIR, "profiles")


def ensure_profile_dir():
    try:
        os.makedirs(PROFILE_DIR, exist_ok=True)
    except Exception:
        pass


def list_profiles():
    ensure_profile_dir()
    try:
        return sorted([f[:-5] for f in os.listdir(PROFILE_DIR) if f.endswith(".json")])
    except Exception:
        return []


def _safe_name(name):
    return "".join(c for c in name if c.isalnum() or c in "_- .").strip()


def save_profile(name):
    name = _safe_name(name)
    if not name:
        return False
    ensure_profile_dir()
    path = os.path.join(PROFILE_DIR, f"{name}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, indent=4, ensure_ascii=False)
        return True
    except Exception:
        return False


def load_profile(name):
    name = _safe_name(name)
    if not name:
        return False
    path = os.path.join(PROFILE_DIR, f"{name}.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            config.apply_dict(json.load(f))
        return True
    except Exception:
        return False


def delete_profile(name):
    name = _safe_name(name)
    path = os.path.join(PROFILE_DIR, f"{name}.json")
    if os.path.exists(path):
        try:
            os.remove(path)
            return True
        except Exception:
            pass
    return False


# --- 多言語（前回と同じ辞書を保持） ---
TRANSLATIONS = {
    "en": {
        "brand": "XENARION", "brand_sub": "AI // AIM CORE",
        "standby": "STANDBY", "engaged": "ENGAGED",
        "overlay_standby": "STANDBY", "overlay_engaged": "ENGAGED",
        "status_hold": "HOLD", "status_toggle": "TOGGLE", "status_always": "ALWAYS",
        "nav_label": "/// NAVIGATE ///",
        "nav_aim": "Aim Config", "nav_target": "Targeting",
        "nav_activation": "Activation", "nav_system": "System",
        "dash_fps": "INFERENCE FPS", "dash_latency": "LATENCY",
        "dash_targets": "TARGETS", "dash_session": "SESSION",
        "dash_fps_unit": "FRAMES / SEC", "dash_latency_unit": "TOTAL PIPELINE",
        "dash_targets_unit": "DETECTIONS", "dash_session_unit": "UPTIME",
        "section_tracking": "TRACKING", "section_anti_recoil": "ANTI-RECOIL",
        "section_targeting": "TARGETING", "section_lock": "TARGET LOCK",
        "section_activation": "ACTIVATION MODE", "section_keybind": "KEY BINDING",
        "section_system": "SYSTEM", "section_profile": "PROFILE",
        "lbl_track_strength": "Track Strength", "lbl_track_x": "X Multiplier",
        "lbl_track_y": "Y Multiplier", "lbl_smoothing": "Smoothing",
        "lbl_deadzone": "Deadzone (px)", "lbl_recoil": "Recoil Compensation",
        "lbl_head_offset": "Head Offset", "lbl_fov_radius": "FOV Radius (px)",
        "lbl_confidence": "Confidence", "lbl_lock_frames": "Lock Frames",
        "lbl_iou": "IoU Threshold", "lbl_ignore_fov": "Ignore targets outside FOV",
        "lbl_debug": "Debug Display (BBox / Cross)",
        "lbl_activation_desc": "Choose when the AI should activate.",
        "mode_hold_btn": "Hold", "mode_toggle_btn": "Toggle", "mode_always_btn": "Always",
        "lbl_keybind_desc": "Click the button and press the key to assign.",
        "lbl_aim_key": "Activation Key", "btn_press_key": "Press a key...",
        "info_all": "・\"Hold\": Active while key is held down\n・\"Toggle\": Press once to toggle ON/OFF\n・\"Always\": Always active regardless of key",
        "lbl_capture_size": "Capture Size", "lbl_polling_rate": "Polling Rate",
        "lbl_fps_limit": "FPS Limit", "lbl_profile_name": "Profile Name",
        "btn_profile_save": "Save", "btn_profile_load": "Load", "btn_profile_delete": "Delete",
        "btn_save": "SAVE SETTINGS", "hotkey_hint": "[F9] HIDE UI",
        "footer_model": "MODEL", "footer_input": "INPUT", "footer_backend": "BACKEND",
    },
    "jp": {
        "brand": "XENARION", "brand_sub": "AI // 照準コア",
        "standby": "待機", "engaged": "作動中",
        "overlay_standby": "待機", "overlay_engaged": "作動中",
        "status_hold": "HOLD", "status_toggle": "TOGGLE", "status_always": "ALWAYS",
        "nav_label": "/// ナビゲート ///",
        "nav_aim": "照準設定", "nav_target": "ターゲット",
        "nav_activation": "発動条件", "nav_system": "システム",
        "dash_fps": "推論FPS", "dash_latency": "レイテンシ",
        "dash_targets": "ターゲット数", "dash_session": "セッション",
        "dash_fps_unit": "フレーム / 秒", "dash_latency_unit": "パイプライン合計",
        "dash_targets_unit": "検出数", "dash_session_unit": "稼働時間",
        "section_tracking": "追従設定", "section_anti_recoil": "反動制御",
        "section_targeting": "ターゲット設定", "section_lock": "ロック設定",
        "section_activation": "発動モード", "section_keybind": "キー割り当て",
        "section_system": "システム設定", "section_profile": "プロファイル",
        "lbl_track_strength": "追従強度", "lbl_track_x": "横倍率",
        "lbl_track_y": "縦倍率", "lbl_smoothing": "スムージング",
        "lbl_deadzone": "デッドゾーン (px)", "lbl_recoil": "反動補正",
        "lbl_head_offset": "頭部オフセット", "lbl_fov_radius": "FOV半径 (px)",
        "lbl_confidence": "信頼度閾値", "lbl_lock_frames": "ロックフレーム数",
        "lbl_iou": "IoU 閾値", "lbl_ignore_fov": "FOV外のターゲットを無視",
        "lbl_debug": "デバッグ表示 (BBox / 十字)",
        "lbl_activation_desc": "AIの作動条件を選択してください。",
        "mode_hold_btn": "押している間", "mode_toggle_btn": "トグル", "mode_always_btn": "常時ON",
        "lbl_keybind_desc": "ボタンをクリックして、割り当てたいキーを押してください。",
        "lbl_aim_key": "作動キー", "btn_press_key": "キーを押してください…",
        "info_all": "・「押している間」: キーを押している間だけ作動\n・「トグル」: キーを押すたびにON/OFFを切り替え\n・「常時ON」: キーに関係なく常に作動",
        "lbl_capture_size": "キャプチャサイズ", "lbl_polling_rate": "ポーリングレート",
        "lbl_fps_limit": "FPS 上限", "lbl_profile_name": "プロファイル名",
        "btn_profile_save": "保存", "btn_profile_load": "読込", "btn_profile_delete": "削除",
        "btn_save": "設定を保存", "hotkey_hint": "[F9] UI非表示",
        "footer_model": "モデル", "footer_input": "入力", "footer_backend": "実行環境",
    },
    "kr": {
        "brand": "XENARION", "brand_sub": "AI // 에임 코어",
        "standby": "대기", "engaged": "작동 중",
        "overlay_standby": "대기", "overlay_engaged": "작동",
        "status_hold": "HOLD", "status_toggle": "TOGGLE", "status_always": "ALWAYS",
        "nav_label": "/// 내비게이트 ///",
        "nav_aim": "조준 설정", "nav_target": "타겟",
        "nav_activation": "발동 조건", "nav_system": "시스템",
        "dash_fps": "추론 FPS", "dash_latency": "지연시간",
        "dash_targets": "타겟 수", "dash_session": "세션",
        "dash_fps_unit": "프레임 / 초", "dash_latency_unit": "파이프라인 합계",
        "dash_targets_unit": "검출 수", "dash_session_unit": "가동 시간",
        "section_tracking": "추종 설정", "section_anti_recoil": "반동 제어",
        "section_targeting": "타겟 설정", "section_lock": "락 설정",
        "section_activation": "발동 모드", "section_keybind": "키 할당",
        "section_system": "시스템 설정", "section_profile": "프로필",
        "lbl_track_strength": "추종 강도", "lbl_track_x": "가로 배율",
        "lbl_track_y": "세로 배율", "lbl_smoothing": "스무딩",
        "lbl_deadzone": "데드존 (px)", "lbl_recoil": "반동 보정",
        "lbl_head_offset": "헤드 오프셋", "lbl_fov_radius": "FOV 반경 (px)",
        "lbl_confidence": "신뢰도 임계값", "lbl_lock_frames": "락 프레임 수",
        "lbl_iou": "IoU 임계값", "lbl_ignore_fov": "FOV 외 타겟 무시",
        "lbl_debug": "디버그 표시 (BBox / 십자)",
        "lbl_activation_desc": "AI 작동 조건을 선택하세요.",
        "mode_hold_btn": "누르는 동안", "mode_toggle_btn": "토글", "mode_always_btn": "항상 켜기",
        "lbl_keybind_desc": "버튼을 클릭하고 할당할 키를 누르세요.",
        "lbl_aim_key": "작동 키", "btn_press_key": "키를 누르세요...",
        "info_all": "・\"누르는 동안\": 키를 누르는 동안만 작동\n・\"토글\": 누를 때마다 ON/OFF 전환\n・\"항상 켜기\": 키와 관계없이 항상 작동",
        "lbl_capture_size": "캡처 크기", "lbl_polling_rate": "폴링 속도",
        "lbl_fps_limit": "FPS 제한", "lbl_profile_name": "프로필 이름",
        "btn_profile_save": "저장", "btn_profile_load": "불러오기", "btn_profile_delete": "삭제",
        "btn_save": "설정 저장", "hotkey_hint": "[F9] UI 숨기기",
        "footer_model": "모델", "footer_input": "입력", "footer_backend": "백엔드",
    },
}


class I18n:
    def __init__(self):
        self.lang = config.language

    def tr(self, key):
        return TRANSLATIONS.get(self.lang, {}).get(key) or TRANSLATIONS["en"].get(key, key)

    def set_lang(self, lang):
        if lang in TRANSLATIONS:
            self.lang = lang
            config.language = lang


i18n = I18n()


# ============================================================
# 発動条件マネージャ
# ============================================================
class ActivationManager:
    def __init__(self):
        self.toggled_on = False
        self.last_state = False

    def is_active(self):
        mode = config.activation_mode
        if mode == "always":
            return True
        state = bool(win32api.GetAsyncKeyState(config.aim_key) & 0x8000)
        if mode == "hold":
            return state
        if mode == "toggle":
            if state and not self.last_state:
                self.toggled_on = not self.toggled_on
            self.last_state = state
            return self.toggled_on
        return False


# ============================================================
# モデル
# ============================================================
MODEL_MAP = {320: "Somewhere_320.onnx", 480: "Somewhere_480.onnx",
             640: "Somewhere_640.onnx", 960: "Somewhere_960.onnx"}


def find_initial_model():
    for size in [320, 480, 640, 960]:
        path = os.path.join(BASE_DIR, MODEL_MAP[size])
        if os.path.isfile(path):
            return path
    path = os.path.join(BASE_DIR, "Somewhere.onnx")
    return path if os.path.isfile(path) else None


def create_session(model_path):
    so = ort.SessionOptions()
    so.enable_mem_pattern = False
    so.enable_cpu_mem_arena = False
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    providers = []
    if "DmlExecutionProvider" in ort.get_available_providers():
        providers.append(("DmlExecutionProvider", {"performance_preference": "high_performance"}))
    providers.append("CPUExecutionProvider")
    return ort.InferenceSession(model_path, sess_options=so, providers=providers)


MODEL_PATH = find_initial_model()
if MODEL_PATH is None:
    print("[エラー] ONNXモデルが見つかりません")
    sys.exit(1)

session = create_session(MODEL_PATH)
input_meta = session.get_inputs()[0]
input_name = input_meta.name
input_shape = input_meta.shape
input_h = input_shape[2] if isinstance(input_shape[2], int) else 320
input_w = input_shape[3] if isinstance(input_shape[3], int) else 320

if input_w in MODEL_MAP:
    config.capture_size = input_w
    config.fov_radius = min(config.fov_radius, input_w // 2)

screen_w, screen_h = 1920, 1080
camera = dxcam.create(output_idx=0, output_color="BGR")


def get_capture_region():
    left = (screen_w - config.capture_size) // 2
    top = (screen_h - config.capture_size) // 2
    return (left, top, left + config.capture_size, top + config.capture_size)


# ============================================================
# エイムロジック
# ============================================================
class AimLogic:
    def __init__(self):
        self.smooth_x = None
        self.smooth_y = None
        self.locked_bbox = None
        self.lock_counter = 0
        self.accum_dx = 0.0
        self.accum_dy = 0.0
        self.last_mouse_time = 0.0
        self.activation = ActivationManager()

    def _bbox_iou(self, a, b):
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        area_a = (ax2 - ax1) * (ay2 - ay1)
        area_b = (bx2 - bx1) * (by2 - by1)
        return inter / (area_a + area_b - inter + 1e-9)

    def _target_from_bbox(self, bbox, fw, fh):
        x1, y1, x2, y2 = bbox
        ratio = max(-3.0, min(10.0, 0.5 + config.head_offset_y * 0.5))
        return ((x1 + x2) / 2, y1 + (y2 - y1) * ratio)

    def process(self, detections, frame_w, frame_h):
        if not config.enabled or not self.activation.is_active():
            return None
        fov_r = config.fov_radius if config.fov_filter_enabled else float("inf")
        cx, cy = frame_w / 2, frame_h / 2
        valid = []
        for det in detections:
            tx, ty = self._target_from_bbox(det["bbox"], frame_w, frame_h)
            if math.hypot(tx - cx, ty - cy) <= fov_r:
                valid.append(det)
        if not valid:
            self.locked_bbox = None
            self.lock_counter = 0
            return None

        target_det = None
        if self.locked_bbox is not None and self.lock_counter > 0:
            best_iou, best_match = 0.0, None
            for det in valid:
                iou = self._bbox_iou(self.locked_bbox, det["bbox"])
                if iou > best_iou:
                    best_iou, best_match = iou, det
            if best_iou > config.lock_iou_threshold:
                target_det = best_match
                self.locked_bbox = best_match["bbox"]
                self.lock_counter -= 1
            else:
                self.locked_bbox = None
                self.lock_counter = 0

        if target_det is None:
            best_dist = float("inf")
            for det in valid:
                tx, ty = self._target_from_bbox(det["bbox"], frame_w, frame_h)
                d = math.hypot(tx - cx, ty - cy)
                if d < best_dist:
                    best_dist, target_det = d, det
            if target_det:
                self.locked_bbox = target_det["bbox"]
                self.lock_counter = config.lock_frames

        if not target_det:
            return None

        target_x, target_y = self._target_from_bbox(target_det["bbox"], frame_w, frame_h)
        if math.hypot(target_x - cx, target_y - cy) < config.deadzone_px:
            self.accum_dx = self.accum_dy = 0.0
            return (target_x, target_y)

        if config.smoothing > 0.0:
            if self.smooth_x is None:
                self.smooth_x, self.smooth_y = target_x, target_y
            else:
                a = config.smoothing
                self.smooth_x = a * self.smooth_x + (1 - a) * target_x
                self.smooth_y = a * self.smooth_y + (1 - a) * target_y
            tx, ty = self.smooth_x, self.smooth_y
        else:
            self.smooth_x = self.smooth_y = None
            tx, ty = target_x, target_y

        now = time.perf_counter()
        if now - self.last_mouse_time < 1.0 / config.polling_rate:
            return (tx, ty)
        self.last_mouse_time = now

        dx = (tx - cx) * config.track_strength * config.track_x
        dy = (ty - cy) * config.track_strength * config.track_y - config.recoil_compensation
        self.accum_dx += dx
        self.accum_dy += dy
        ix, iy = int(self.accum_dx), int(self.accum_dy)
        if ix != 0 or iy != 0:
            self.accum_dx -= ix
            self.accum_dy -= iy
            send_mouse_move(ix, iy)
        return (tx, ty)


# ============================================================
# 推論ワーカー
# ============================================================
class InferenceWorker(QThread):
    state_ready = pyqtSignal(list, object, bool)
    stats_ready = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.running = True
        self.aim_logic = AimLogic()
        self._ema_fps = 0.0
        self._ema_alpha = 0.15

    def run(self):
        dummy = np.random.randn(1, 3, input_h, input_w).astype(np.float32)
        for _ in range(5):
            session.run(None, {input_name: dummy})
        camera.start(region=get_capture_region(),
                     target_fps=CAPTURE_TARGET_FPS, video_mode=True)

        pre_ms = inf_ms = post_ms = 0.0
        while self.running:
            loop_start = time.perf_counter()
            frame = camera.get_latest_frame()
            if frame is None:
                time.sleep(0.001)
                continue
            t0 = time.perf_counter()
            blob = cv2.dnn.blobFromImage(frame, 1.0 / 255.0, (input_w, input_h),
                                         (0, 0, 0), swapRB=True, crop=False)
            t1 = time.perf_counter()
            try:
                outputs = session.run(None, {input_name: blob})
            except Exception:
                continue
            t2 = time.perf_counter()
            dets = self._postprocess(outputs, frame.shape[1], frame.shape[0])
            t3 = time.perf_counter()

            pre_ms = (t1 - t0) * 1000
            inf_ms = (t2 - t1) * 1000
            post_ms = (t3 - t2) * 1000
            active = self.aim_logic.activation.is_active()
            target = self.aim_logic.process(dets, frame.shape[1], frame.shape[0])
            self.state_ready.emit(dets, target, active)

            if config.fps_limit > 0:
                target_interval = 1.0 / config.fps_limit
                elapsed = time.perf_counter() - loop_start
                remain = target_interval - elapsed
                if remain > 0:
                    time.sleep(remain)

            loop_total = time.perf_counter() - loop_start
            instant_fps = 1.0 / loop_total if loop_total > 0 else 0
            if self._ema_fps == 0:
                self._ema_fps = instant_fps
            else:
                self._ema_fps = (1 - self._ema_alpha) * self._ema_fps + self._ema_alpha * instant_fps

            self.stats_ready.emit({
                "pre_ms": pre_ms, "inf_ms": inf_ms,
                "post_ms": post_ms, "fps": self._ema_fps, "dets": len(dets),
            })
        camera.stop()

    def _postprocess(self, outputs, orig_w, orig_h):
        raw = outputs[0]
        if raw.ndim == 3:
            preds = raw[0]
            if preds.shape[0] < preds.shape[1]:
                preds = preds.T
        else:
            preds = raw
        if preds.shape[0] == 0:
            return []
        conf = preds[:, 4:].max(axis=1)
        mask = conf >= config.conf_threshold
        if not mask.any():
            return []
        preds, conf = preds[mask], conf[mask]
        cx, cy, w, h = preds[:, 0], preds[:, 1], preds[:, 2], preds[:, 3]
        sx, sy = orig_w / input_w, orig_h / input_h
        x1, y1 = (cx - w / 2) * sx, (cy - h / 2) * sy
        x2, y2 = (cx + w / 2) * sx, (cy + h / 2) * sy
        order = conf.argsort()[::-1]
        x1, y1, x2, y2, conf = x1[order], y1[order], x2[order], y2[order], conf[order]
        areas = (x2 - x1) * (y2 - y1)
        idxs = np.arange(len(conf))
        keep = []
        while idxs.size > 0:
            i = idxs[0]
            keep.append(i)
            if idxs.size == 1:
                break
            rest = idxs[1:]
            xx1 = np.maximum(x1[i], x1[rest]); yy1 = np.maximum(y1[i], y1[rest])
            xx2 = np.minimum(x2[i], x2[rest]); yy2 = np.minimum(y2[i], y2[rest])
            inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
            iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
            idxs = rest[iou <= config.nms_threshold]
        return [{"bbox": [float(x1[k]), float(y1[k]), float(x2[k]), float(y2[k])],
                 "confidence": float(conf[k])} for k in keep]

    def stop(self):
        self.running = False
        self.wait()


# ============================================================
# FOVオーバーレイ
# ============================================================
class FOVOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(0, 0, screen_w, screen_h)
        self._fps = 0.0
        self._active = False
        self.show()
        QTimer.singleShot(100, self._apply_win32_click_through)

    def _apply_win32_click_through(self):
        hwnd = int(self.winId())
        styles = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(
            hwnd, win32con.GWL_EXSTYLE,
            styles | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
                   | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE)

    def set_stats(self, fps, active):
        self._fps = fps
        self._active = active
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = screen_w // 2, screen_h // 2
        r = config.fov_radius
        p.setPen(QPen(QColor(255, 255, 255, 170), 1))
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)
        p.setPen(QPen(QColor(255, 255, 255, 220), 1))
        p.drawLine(cx - 7, cy, cx + 7, cy)
        p.drawLine(cx, cy - 7, cx, cy + 7)

        font = QFont("Consolas")
        font.setPointSize(12)
        font.setBold(True)
        p.setFont(font)
        fps_text = f"INF {self._fps:6.1f} FPS"
        state_text = (f"◉ {i18n.tr('overlay_engaged')}" if self._active
                      else f"○ {i18n.tr('overlay_standby')}")
        fm = p.fontMetrics()
        fps_w = fm.horizontalAdvance(fps_text)
        state_w = fm.horizontalAdvance(state_text)
        gap = 22
        total_w = fps_w + gap + state_w
        pad_x, bar_h, y_top = 16, 28, 24
        x = (screen_w - total_w) // 2
        baseline = y_top + bar_h - 8

        p.fillRect(QRectF(x - pad_x, y_top, total_w + pad_x * 2, bar_h),
                   QColor(0, 0, 0, 200))
        border_c = QColor(255, 255, 255, 230) if self._active else QColor(255, 255, 255, 90)
        p.setPen(QPen(border_c, 1))
        p.drawRect(QRectF(x - pad_x + 0.5, y_top + 0.5,
                          total_w + pad_x * 2 - 1, bar_h - 1))
        p.setPen(QColor(220, 220, 220))
        p.drawText(int(x), int(baseline), fps_text)
        sep_x = x + fps_w + gap // 2
        p.setPen(QPen(QColor(255, 255, 255, 80), 1))
        p.drawLine(int(sep_x), y_top + 8, int(sep_x), y_top + bar_h - 8)
        p.setPen(QColor(255, 255, 255) if self._active else QColor(140, 140, 140))
        p.drawText(int(sep_x + gap // 2 + (gap // 2)), int(baseline), state_text)


# ============================================================
# アイコン描画
# ============================================================
def draw_icon(p, rect, icon_type, color, width=1.8):
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(color, width))
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx, cy = rect.center().x(), rect.center().y()
    r = min(rect.width(), rect.height()) * 0.35

    if icon_type == "crosshair":
        p.drawEllipse(QPointF(cx, cy), r, r)
        outer, gap = r + 6, r + 2
        p.drawLine(QPointF(cx - outer, cy), QPointF(cx - gap, cy))
        p.drawLine(QPointF(cx + gap, cy), QPointF(cx + outer, cy))
        p.drawLine(QPointF(cx, cy - outer), QPointF(cx, cy - gap))
        p.drawLine(QPointF(cx, cy + gap), QPointF(cx, cy + outer))
        p.setBrush(color); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), 2, 2)
    elif icon_type == "target":
        for ratio in [1.0, 0.6, 0.25]:
            p.drawEllipse(QPointF(cx, cy), r * ratio, r * ratio)
        p.setBrush(color); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), 2.2, 2.2)
    elif icon_type == "lightning":
        path = QPainterPath()
        path.moveTo(cx + r * 0.3, cy - r)
        path.lineTo(cx - r * 0.4, cy + r * 0.1)
        path.lineTo(cx + r * 0.1, cy + r * 0.1)
        path.lineTo(cx - r * 0.3, cy + r)
        path.lineTo(cx + r * 0.4, cy - r * 0.1)
        path.lineTo(cx - r * 0.1, cy - r * 0.1)
        path.closeSubpath()
        p.setBrush(color); p.setPen(QPen(color, 1))
        p.drawPath(path)
    elif icon_type == "gear":
        path = QPainterPath()
        n = 16
        for i in range(n):
            ang = (i / n) * 2 * math.pi - math.pi / 2
            rad = r if i % 2 == 0 else r * 0.65
            px, py = cx + rad * math.cos(ang), cy + rad * math.sin(ang)
            path.moveTo(px, py) if i == 0 else path.lineTo(px, py)
        path.closeSubpath()
        p.drawPath(path)
        p.drawEllipse(QPointF(cx, cy), r * 0.38, r * 0.38)
    p.restore()


# ============================================================
# ロゴ / 装飾 / カスタムウィジェット（前回と同じ、省略なし）
# ============================================================
class XenarionLogo(QWidget):
    def __init__(self, size=56, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0.0
        self._pulse = 0.0
        self._pulse_dir = 1
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def _tick(self):
        self._angle = (self._angle + 2.4) % 360
        self._pulse += 0.05 * self._pulse_dir
        if self._pulse >= 1.0:
            self._pulse, self._pulse_dir = 1.0, -1
        elif self._pulse <= 0.0:
            self._pulse, self._pulse_dir = 0.0, 1
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy, r = w / 2, h / 2, min(w, h) * 0.42
        glow = QRadialGradient(cx, cy, r * 2.5)
        glow.setColorAt(0, QColor(255, 255, 255, int(60 + 70 * self._pulse)))
        glow.setColorAt(1, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(glow)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, w, h)
        hex_path = QPainterPath()
        for i in range(6):
            ang = math.radians(60 * i - 90)
            x, y = cx + r * math.cos(ang), cy + r * math.sin(ang)
            hex_path.moveTo(x, y) if i == 0 else hex_path.lineTo(x, y)
        hex_path.closeSubpath()
        p.setPen(QPen(QColor(255, 255, 255, 240), 1.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(hex_path)
        p.save(); p.translate(cx, cy); p.rotate(self._angle)
        p.setPen(QPen(QColor(255, 255, 255, 140), 1.2))
        r2 = r * 0.72
        for i in range(3):
            a1, a2 = math.radians(120 * i), math.radians(120 * i + 42)
            p.drawLine(QPointF(r2 * math.cos(a1), r2 * math.sin(a1)),
                       QPointF(r2 * math.cos(a2), r2 * math.sin(a2)))
        p.restore()
        xr = r * 0.40
        p.setPen(QPen(QColor(255, 255, 255, 255), 2.6))
        p.drawLine(QPointF(cx - xr, cy - xr), QPointF(cx + xr, cy + xr))
        p.drawLine(QPointF(cx + xr, cy - xr), QPointF(cx - xr, cy + xr))
        p.setBrush(QColor(255, 255, 255)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), 2, 2)


class BackgroundPattern(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._cache = None
        self._cache_size = None

    def resizeEvent(self, e):
        super().resizeEvent(e); self._cache = None

    def paintEvent(self, e):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        if self._cache is None or self._cache_size != (w, h):
            self._rebuild(w, h)
        if self._cache:
            QPainter(self).drawPixmap(0, 0, self._cache)

    def _rebuild(self, w, h):
        pix = QPixmap(w, h); pix.fill(QColor(5, 5, 5))
        pp = QPainter(pix)
        pp.setPen(QPen(QColor(255, 255, 255, 8), 1))
        for x in range(-h, w + h, 44):
            pp.drawLine(x, 0, x + h, h)
        pp.setPen(QPen(QColor(255, 255, 255, 4), 1))
        for x in range(0, w, 28): pp.drawLine(x, 0, x, h)
        for y in range(0, h, 28): pp.drawLine(0, y, w, y)
        grad = QRadialGradient(w / 2, h / 2, max(w, h) * 0.75)
        grad.setColorAt(0, QColor(0, 0, 0, 0))
        grad.setColorAt(0.7, QColor(0, 0, 0, 60))
        grad.setColorAt(1, QColor(0, 0, 0, 200))
        pp.fillRect(0, 0, w, h, QBrush(grad)); pp.end()
        self._cache, self._cache_size = pix, (w, h)


class ScanlineOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._cache = None
        self._cache_size = None

    def resizeEvent(self, e):
        super().resizeEvent(e); self._cache = None

    def paintEvent(self, e):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        if self._cache is None or self._cache_size != (w, h):
            pix = QPixmap(w, h); pix.fill(Qt.GlobalColor.transparent)
            pp = QPainter(pix); pp.setPen(QPen(QColor(255, 255, 255, 9), 1))
            for y in range(0, h, 6): pp.drawLine(0, y, w, y)
            pp.end(); self._cache, self._cache_size = pix, (w, h)
        QPainter(self).drawPixmap(0, 0, self._cache)


class CornerBracket(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 130), 2))
        w, h, L, m = self.width(), self.height(), 28, 12
        p.drawLine(m, m, m + L, m); p.drawLine(m, m, m, m + L)
        p.drawLine(w - m, m, w - m - L, m); p.drawLine(w - m, m, w - m, m + L)
        p.drawLine(m, h - m, m + L, h - m); p.drawLine(m, h - m, m, h - m - L)
        p.drawLine(w - m, h - m, w - m - L, h - m); p.drawLine(w - m, h - m, w - m, h - m - L)


class AnimatedAccent(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self._pos = 0.0; self._dir = 1
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def _tick(self):
        self._pos += 0.02 * self._dir
        if self._pos >= 1.0: self._pos, self._dir = 1.0, -1
        elif self._pos <= 0.0: self._pos, self._dir = 0.0, 1
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(18, 18, 18))
        x = int(self.width() * self._pos)
        grad = QLinearGradient(x - 280, 0, x + 280, 0)
        grad.setColorAt(0.0, QColor(255, 255, 255, 0))
        grad.setColorAt(0.45, QColor(255, 255, 255, 200))
        grad.setColorAt(0.5, QColor(255, 255, 255, 255))
        grad.setColorAt(0.55, QColor(255, 255, 255, 200))
        grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(self.rect(), QBrush(grad))


class GlitchLabel(QWidget):
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._text = text; self._glitch = 0.0
        self._next_glitch = time.time() + 8.0
        self._glitch_end = 0.0
        self._font_size = 34
        self.setFixedHeight(self._font_size + 14)
        self._font = QFont(self.font())
        self._font.setBold(True); self._font.setPixelSize(self._font_size)
        self._font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 10)
        self._fm = QFontMetrics(self._font)
        self.setFixedWidth(self._fm.horizontalAdvance(self._text) + 24)
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick)
        self._timer.start(100)

    def setText(self, text):
        if text == self._text: return
        self._text = text
        self.setFixedWidth(self._fm.horizontalAdvance(self._text) + 24)
        self.update()

    def _tick(self):
        now = time.time()
        if now >= self._next_glitch and now >= self._glitch_end:
            self._glitch_end = now + 0.18
            self._next_glitch = now + 8.0 + random.random() * 7.0
        self._glitch = 1.0 if now < self._glitch_end else 0.0
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self._font)
        th = self._fm.height()
        y = (self.height() + th) // 2 - self._fm.descent() - 2
        if self._glitch > 0:
            off = 3 + random.randint(-1, 2)
            p.setPen(QColor(255, 255, 255, 100)); p.drawText(off, y, self._text)
            p.setPen(QColor(255, 255, 255, 160)); p.drawText(-off, y, self._text)
        p.setPen(QColor(255, 255, 255)); p.drawText(0, y, self._text)


class AngledCard(QFrame):
    def __init__(self, parent=None, cut=10):
        super().__init__(parent)
        self._cut = cut
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h, c = self.width(), self.height(), self._cut
        path = QPainterPath()
        path.moveTo(c, 0); path.lineTo(w - c, 0); path.lineTo(w, c)
        path.lineTo(w, h - c); path.lineTo(w - c, h); path.lineTo(c, h)
        path.lineTo(0, h - c); path.lineTo(0, c); path.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, QColor(12, 12, 12)); grad.setColorAt(1, QColor(6, 6, 6))
        p.fillPath(path, QBrush(grad))
        p.setPen(QPen(QColor(55, 55, 55), 1)); p.drawPath(path)


def load_fonts():
    families = []
    for fname in ["DotGothic16-Regular.ttf", "MPLUS1p-Regular.ttf",
                  "NotoSansJP-Regular.ttf", "Bit32.ttf", "PressStart2P-Regular.ttf"]:
        path = os.path.join(BASE_DIR, fname)
        if os.path.isfile(path):
            fid = QFontDatabase.addApplicationFont(path)
            if fid >= 0:
                families.extend(QFontDatabase.applicationFontFamilies(fid))
                print(f"[フォント] 読み込み: {fname}")
    return families


# ============================================================
# カスタムウィジェット
# ============================================================
class StatusLED(QWidget):
    def __init__(self, parent=None, size=12, active_color=QColor(255, 255, 255)):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._on = True; self._active_color = active_color; self._active = False
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick)
        self._timer.start(800)

    def _tick(self): self._on = not self._on; self.update()
    def set_active(self, v):
        if self._active != v: self._active = v; self.update()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        base = self._active_color if self._active else QColor(90, 90, 90)
        color = base if self._on or self._active else QColor(base.red()//3, base.green()//3, base.blue()//3)
        if self._active:
            glow = QRadialGradient(self.width()/2, self.height()/2, self.width())
            glow.setColorAt(0, QColor(base.red(), base.green(), base.blue(), 160))
            glow.setColorAt(1, QColor(base.red(), base.green(), base.blue(), 0))
            p.setBrush(QBrush(glow)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(0, 0, self.width(), self.height())
        p.setBrush(color); p.setPen(Qt.PenStyle.NoPen)
        m = self.width()//6
        p.drawEllipse(m, m, self.width() - m*2, self.height() - m*2)


class NeoSlider(QWidget):
    valueChanged = pyqtSignal(float)

    def __init__(self, mn, mx, default, step=0.01, integer=False, parent=None):
        super().__init__(parent)
        self._min, self._max = mn, mx
        self._step, self._integer = step, integer
        self._value = default; self._hover = False; self._dragging = False
        self._flash = 0.0
        self.setFixedHeight(28); self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"flash", self)
        self._anim.setDuration(400); self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0); self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    @pyqtProperty(float)
    def flash(self): return self._flash
    @flash.setter
    def flash(self, v): self._flash = v; self.update()

    def set_value(self, v): self._set_value(v)
    def _pos_to_value(self, x):
        w = self.width() - 20
        if w <= 0: return self._min
        ratio = max(0.0, min(1.0, (x - 10) / w))
        v = self._min + ratio * (self._max - self._min)
        v = round(v) if self._integer else round(v / self._step) * self._step
        return max(self._min, min(self._max, v))
    def _value_to_ratio(self):
        return 0.0 if self._max == self._min else (self._value - self._min)/(self._max - self._min)
    def _set_value(self, v):
        v = max(self._min, min(self._max, v))
        if abs(v - self._value) < 1e-9: return
        self._value = v; self._anim.stop(); self._anim.start()
        self.valueChanged.emit(v); self.update()
    def enterEvent(self, e): self._hover = True; self.update()
    def leaveEvent(self, e): self._hover = False; self.update()
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True; self._set_value(self._pos_to_value(e.position().x()))
    def mouseMoveEvent(self, e):
        if self._dragging: self._set_value(self._pos_to_value(e.position().x()))
    def mouseReleaseEvent(self, e): self._dragging = False

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height(); ty = h // 2; l, r = 10, w - 10
        path = QPainterPath(); path.addRoundedRect(QRectF(l, ty - 1, r - l, 2), 1, 1)
        p.fillPath(path, QColor(40, 40, 40))
        fw = (r - l) * self._value_to_ratio()
        if fw > 0:
            path = QPainterPath(); path.addRoundedRect(QRectF(l, ty - 1, fw, 2), 1, 1)
            grad = QLinearGradient(l, 0, l + fw, 0)
            grad.setColorAt(0.0, QColor(130, 130, 130)); grad.setColorAt(1.0, QColor(255, 255, 255))
            p.fillPath(path, QBrush(grad))
        hx = l + fw
        if self._flash > 0:
            gr = 8 + self._flash * 16
            p.setBrush(QColor(255, 255, 255, int(90 * self._flash))); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(hx, ty), gr, gr)
        size = 14 if (self._hover or self._dragging) else 11
        p.setBrush(QColor(255, 255, 255)); p.setPen(QPen(QColor(0, 0, 0), 1))
        p.drawRect(QRectF(hx - size/2, ty - size/2, size, size))
        p.setPen(QPen(QColor(70, 70, 70), 1))
        p.drawLine(l, ty - 5, l, ty + 5); p.drawLine(r, ty - 5, r, ty + 5)


class NeoToggle(QWidget):
    toggled = pyqtSignal(bool)
    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self._checked = checked; self._pos = 1.0 if checked else 0.0
        self.setFixedSize(46, 22); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(180); self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    @pyqtProperty(float)
    def knobPos(self): return self._pos
    @knobPos.setter
    def knobPos(self, v): self._pos = v; self.update()
    def isChecked(self): return self._checked
    def setChecked(self, v):
        self._checked = bool(v); self._pos = 1.0 if self._checked else 0.0; self.update()
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._checked = not self._checked
            self._anim.stop(); self._anim.setStartValue(self._pos)
            self._anim.setEndValue(1.0 if self._checked else 0.0); self._anim.start()
            self.toggled.emit(self._checked)
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if self._checked:
            p.setBrush(QColor(255, 255, 255)); p.setPen(QPen(QColor(255, 255, 255), 1))
        else:
            p.setBrush(QColor(22, 22, 22)); p.setPen(QPen(QColor(80, 80, 80), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), h/2, h/2)
        kd = h - 6; x = 3 + self._pos * (w - kd - 6)
        p.setBrush(QColor(0, 0, 0) if self._checked else QColor(200, 200, 200))
        p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(QRectF(x, 3, kd, kd))


class SegmentButton(QWidget):
    changed = pyqtSignal(str)
    def __init__(self, options, current, parent=None):
        super().__init__(parent)
        self._options, self._current = options, current
        self._hover_idx = -1; self.setFixedHeight(38)
        self.setMouseTracking(True); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim_offset = 0.0; self._target_idx = self._current_index()
        self._anim = QPropertyAnimation(self, b"animOffset", self)
        self._anim.setDuration(240); self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    def _current_index(self):
        for i, (k, _) in enumerate(self._options):
            if k == self._current: return i
        return 0
    @pyqtProperty(float)
    def animOffset(self): return self._anim_offset
    @animOffset.setter
    def animOffset(self, v): self._anim_offset = v; self.update()
    def setCurrent(self, key):
        if key == self._current: return
        self._current = key; self._target_idx = self._current_index()
        self._anim.stop(); self._anim.setStartValue(self._anim_offset)
        self._anim.setEndValue(float(self._target_idx)); self._anim.start()
        self.changed.emit(key)
    def update_options(self, options):
        self._options = options
        keys = [k for k, _ in options]
        if self._current not in keys: self._current = keys[0]
        self._target_idx = self._current_index(); self._anim_offset = float(self._target_idx)
        self.update()
    def mousePressEvent(self, e):
        idx = self._index_at(e.position().x())
        if idx >= 0: self.setCurrent(self._options[idx][0])
    def mouseMoveEvent(self, e):
        idx = self._index_at(e.position().x())
        if idx != self._hover_idx: self._hover_idx = idx; self.update()
    def leaveEvent(self, e): self._hover_idx = -1; self.update()
    def _index_at(self, x):
        n = len(self._options); sw = self.width() / n
        return max(0, min(n - 1, int(x / sw)))
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height(); n = len(self._options); sw = w / n
        p.setBrush(QColor(15, 15, 15)); p.setPen(QPen(QColor(55, 55, 55), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 4, 4)
        x = self._anim_offset * sw
        p.setBrush(QColor(255, 255, 255)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(x + 3, 3, sw - 6, h - 6), 3, 3)
        for i, (_, label) in enumerate(self._options):
            active = i == int(round(self._anim_offset))
            color = QColor(0, 0, 0) if active else (QColor(220, 220, 220) if i == self._hover_idx else QColor(130, 130, 130))
            f = QFont(); f.setPointSize(9); f.setBold(active)
            p.setFont(f); p.setPen(color)
            p.drawText(QRectF(i * sw, 0, sw, h), Qt.AlignmentFlag.AlignCenter, label)


class KeyBinderWidget(QWidget):
    keyChanged = pyqtSignal(int)
    def __init__(self, current_key, parent=None):
        super().__init__(parent)
        self._key = current_key; self._capturing = False; self._blink = True
        self._capture_start = 0.0; self._keys_at_start = set()
        self.setFixedHeight(36); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(140)
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick)
        self._timer.start(20)
        self._blink_timer = QTimer(self); self._blink_timer.timeout.connect(self._blink_tick)
        self._blink_timer.start(450)
    def _blink_tick(self):
        if self._capturing: self._blink = not self._blink; self.update()
    def _tick(self):
        if not self._capturing: return
        if time.perf_counter() - self._capture_start < 0.2: return
        new_keys = snapshot_pressed_keys() - self._keys_at_start - {0x01}
        if new_keys:
            vk = min(new_keys); self._key = vk; self._capturing = False
            self._blink = True; self.keyChanged.emit(vk); self.update()
            print(f"[キー割り当て] 確定: {get_key_name(vk)}")
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._capturing = True; self._blink = True
            self._capture_start = time.perf_counter()
            self._keys_at_start = snapshot_pressed_keys(); self.update()
    def set_key(self, vk): self._key = vk; self.update()
    def update_language(self): self.update()
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if self._capturing:
            border = QColor(255, 255, 255) if self._blink else QColor(120, 120, 120)
            bg = QColor(35, 35, 35)
        else:
            border = QColor(80, 80, 80); bg = QColor(15, 15, 15)
        p.setBrush(bg); p.setPen(QPen(border, 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 4, 4)
        f = QFont(); f.setPointSize(10); f.setBold(True); p.setFont(f)
        if self._capturing:
            p.setPen(QColor(255, 255, 255) if self._blink else QColor(130, 130, 130))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, i18n.tr("btn_press_key"))
        else:
            p.setPen(QColor(255, 255, 255))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"⌨  {get_key_name(self._key)}")


class ValueLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setFixedWidth(72); self._flash = 0.0; self._apply_style(0.0)
        self._anim = QPropertyAnimation(self, b"flash", self)
        self._anim.setDuration(350); self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0); self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    def _apply_style(self, v):
        c = int(150 + 105 * v)
        self.setStyleSheet(f"color: rgb({c},{c},{c}); font-weight: bold; font-size: 13px; font-family: 'Consolas', monospace;")
    @pyqtProperty(float)
    def flash(self): return self._flash
    @flash.setter
    def flash(self, v): self._flash = v; self._apply_style(v)
    def setText(self, t):
        if t != self.text(): self._anim.stop(); self._anim.start()
        super().setText(t)


class SideBarButton(QAbstractButton):
    def __init__(self, text, icon_type, parent=None):
        super().__init__(parent)
        self.setText(text); self._icon_type = icon_type; self._hover = False
        self._indicator = 0.0
        self._anim = QPropertyAnimation(self, b"indicatorPos", self)
        self._anim.setDuration(220); self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setCheckable(True); self.setFixedHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
    @pyqtProperty(float)
    def indicatorPos(self): return self._indicator
    @indicatorPos.setter
    def indicatorPos(self, v): self._indicator = v; self.update()
    def enterEvent(self, e): self._hover = True; self.update()
    def leaveEvent(self, e): self._hover = False; self.update()
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height(); checked = self.isChecked()
        if checked:
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0, QColor(32, 32, 32)); grad.setColorAt(1, QColor(14, 14, 14))
            p.fillRect(self.rect(), QBrush(grad))
        elif self._hover:
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0, QColor(20, 20, 20)); grad.setColorAt(1, QColor(8, 8, 8))
            p.fillRect(self.rect(), QBrush(grad))
        else:
            p.fillRect(self.rect(), QColor(6, 6, 6))
        if checked: p.fillRect(0, 0, 4, h, QColor(255, 255, 255))
        draw_icon(p, QRectF(20, 0, 30, h), self._icon_type,
                  QColor(255, 255, 255) if checked else QColor(110, 110, 110),
                  width=2.0 if checked else 1.6)
        f = QFont(); f.setPointSize(10); f.setBold(checked); p.setFont(f)
        p.setPen(QColor(255, 255, 255) if checked else QColor(150, 150, 150))
        p.drawText(QRectF(56, 0, w - 56, h),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())


class MetricCard(QWidget):
    def __init__(self, label, value="0", icon_type=None, sub="", parent=None):
        super().__init__(parent)
        l = QVBoxLayout(self); l.setContentsMargins(16, 8, 8, 8); l.setSpacing(0)
        top = QHBoxLayout(); top.setSpacing(6)
        if icon_type: top.addWidget(MetricIcon(icon_type))
        self._label = QLabel(label)
        self._label.setStyleSheet("color: #7a7a7a; font-size: 10px; letter-spacing: 3px; font-weight: bold;")
        top.addWidget(self._label); top.addStretch(); l.addLayout(top)
        self._value = QLabel(value)
        self._value.setStyleSheet("color: #ffffff; font-size: 34px; font-weight: 900; font-family: 'Consolas', monospace; letter-spacing: 1px;")
        l.addWidget(self._value)
        self._sub = QLabel(sub); self._sub.setStyleSheet("color: #404040; font-size: 8px; letter-spacing: 2px;")
        l.addWidget(self._sub)
    def set_value(self, text): self._value.setText(text)
    def set_label(self, text): self._label.setText(text)
    def set_sub(self, text): self._sub.setText(text)
    def setText(self, text): self._label.setText(text)
    def paintEvent(self, e):
        p = QPainter(self); h = self.height()
        p.fillRect(0, 0, 3, h, QColor(255, 255, 255, 200))
        p.fillRect(0, 0, 1, h, QColor(255, 255, 255, 255))


class MetricIcon(QWidget):
    def __init__(self, icon_type, parent=None):
        super().__init__(parent); self._icon_type = icon_type; self.setFixedSize(14, 14)
    def paintEvent(self, e):
        p = QPainter(self)
        draw_icon(p, QRectF(0, 0, 14, 14), self._icon_type, QColor(180, 180, 180), width=1.4)


class LanguageSelector(QWidget):
    changed = pyqtSignal(str)
    def __init__(self, current="jp", parent=None):
        super().__init__(parent)
        self._current = current; self._options = [("en", "EN"), ("jp", "JP"), ("kr", "KR")]
        self._hover_idx = -1; self.setFixedSize(102, 28)
        self.setMouseTracking(True); self.setCursor(Qt.CursorShape.PointingHandCursor)
    def mousePressEvent(self, e):
        sw = self.width() / len(self._options)
        idx = max(0, min(len(self._options) - 1, int(e.position().x() / sw)))
        key = self._options[idx][0]
        if key != self._current: self._current = key; self.changed.emit(key); self.update()
    def mouseMoveEvent(self, e):
        sw = self.width() / len(self._options)
        idx = max(0, min(len(self._options) - 1, int(e.position().x() / sw)))
        if idx != self._hover_idx: self._hover_idx = idx; self.update()
    def leaveEvent(self, e): self._hover_idx = -1; self.update()
    def set_lang(self, lang): self._current = lang; self.update()
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height(); sw = w / len(self._options)
        p.setBrush(QColor(15, 15, 15)); p.setPen(QPen(QColor(60, 60, 60), 1))
        p.drawRect(0, 0, w, h)
        for i, (key, label) in enumerate(self._options):
            x = i * sw
            if key == self._current:
                p.fillRect(QRectF(x, 0, sw, h), QColor(255, 255, 255))
                p.setPen(QColor(0, 0, 0))
            elif i == self._hover_idx: p.setPen(QColor(220, 220, 220))
            else: p.setPen(QColor(120, 120, 120))
            f = QFont(); f.setPointSize(9); f.setBold(True); p.setFont(f)
            p.drawText(QRectF(x, 0, sw, h), Qt.AlignmentFlag.AlignCenter, label)


# ============================================================
# ComboBox スタイル
# ============================================================
COMBO_POPUP_QSS = """
QAbstractItemView { background-color: #0a0a0a; color: #ffffff;
  selection-background-color: #ffffff; selection-color: #000000;
  border: 1px solid #5a5a5a; outline: 0px; padding: 4px; font-size: 12px; }
QAbstractItemView::item { color: #ffffff; background-color: transparent;
  min-height: 26px; padding: 4px 10px; }
QAbstractItemView::item:hover { background-color: #2a2a2a; color: #ffffff; }
QAbstractItemView::item:selected { background-color: #ffffff; color: #000000; }
"""


def apply_combo_style(combo):
    view = combo.view()
    view.setStyleSheet(COMBO_POPUP_QSS)
    pal = view.palette()
    pal.setColor(pal.ColorRole.Base, QColor(10, 10, 10))
    pal.setColor(pal.ColorRole.Text, QColor(255, 255, 255))
    pal.setColor(pal.ColorRole.Window, QColor(10, 10, 10))
    pal.setColor(pal.ColorRole.WindowText, QColor(255, 255, 255))
    pal.setColor(pal.ColorRole.Highlight, QColor(255, 255, 255))
    pal.setColor(pal.ColorRole.HighlightedText, QColor(0, 0, 0))
    view.setPalette(pal)


# ============================================================
# ゲームフォーカス / ホットキー
# ============================================================
def focus_game_window():
    found = []
    def enum_cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd): return True
        title = win32gui.GetWindowText(hwnd)
        for c in ["Overwatch", "overwatch", "OW2"]:
            if c.lower() in title.lower(): found.append(hwnd); break
        return True
    win32gui.EnumWindows(enum_cb, None)
    if found:
        try:
            win32gui.ShowWindow(found[0], win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(found[0])
        except Exception: pass


class HotkeyWatcher(QThread):
    toggle_ui = pyqtSignal()
    def __init__(self):
        super().__init__(); self.running = True; self._last = False
    def run(self):
        while self.running:
            s = bool(win32api.GetAsyncKeyState(win32con.VK_F9) & 0x8000)
            if s and not self._last: self.toggle_ui.emit()
            self._last = s; time.sleep(0.05)
    def stop(self): self.running = False; self.wait()


# ============================================================
# メインウィンドウ
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self, font_family):
        super().__init__()
        self.setWindowTitle("XENARION AI // AIM CORE")
        self.resize(1420, 920)
        self.font_family = font_family
        self.setStyleSheet(self._build_qss())
        self.start_time = time.time()
        self._current_fps = 0.0; self._current_active = False
        self._i18n_items = []; self._slider_refs = {}

        self.worker = InferenceWorker()
        self.worker.state_ready.connect(self.on_state)
        self.worker.stats_ready.connect(self.on_stats)
        self.worker.start()

        self.fov_overlay = FOVOverlay()
        self.clock_timer = QTimer(self); self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(1000)
        self.uptime_timer = QTimer(self); self.uptime_timer.timeout.connect(self._update_uptime)
        self.uptime_timer.start(1000)
        self.overlay_timer = QTimer(self); self.overlay_timer.timeout.connect(self._update_overlay)
        self.overlay_timer.start(150)
        self.hotkey = HotkeyWatcher()
        self.hotkey.toggle_ui.connect(self.toggle_ui_visibility)
        self.hotkey.start()
        self._build_ui()

    # --- i18n / 時計 / 状態 ---
    def _apply_to(self, widget, key):
        text = i18n.tr(key)
        if isinstance(widget, MetricCard): widget.set_label(text)
        elif isinstance(widget, GlitchLabel): widget.setText(text)
        elif hasattr(widget, "setTitle") and not isinstance(widget, (QLabel, QPushButton)):
            widget.setTitle(text)
        else: widget.setText(text)

    def _apply_language(self):
        for widget, key in self._i18n_items:
            try: self._apply_to(widget, key)
            except Exception as e: print(f"[WARN] i18n {key}: {e}")
        self._update_mode_label(); self._update_state_label(); self._update_model_info()
        self.metric_fps.set_sub(i18n.tr("dash_fps_unit"))
        self.metric_lat.set_sub(i18n.tr("dash_latency_unit"))
        self.metric_dets.set_sub(i18n.tr("dash_targets_unit"))
        self.metric_uptime.set_sub(i18n.tr("dash_session_unit"))
        if hasattr(self, "segment"):
            self.segment.update_options([
                ("hold", i18n.tr("mode_hold_btn")),
                ("toggle", i18n.tr("mode_toggle_btn")),
                ("always", i18n.tr("mode_always_btn"))])
            self.segment._current = config.activation_mode
            self.segment._anim_offset = float(self.segment._current_index())
            self.segment.update()
        if hasattr(self, "key_binder"): self.key_binder.update_language()

    def _on_language_change(self, lang):
        i18n.set_lang(lang); self._apply_language()

    def _update_clock(self):
        if hasattr(self, "clock_label"):
            self.clock_label.setText(datetime.now().strftime("%H:%M:%S"))

    def _update_uptime(self):
        if hasattr(self, "metric_uptime"):
            e = int(time.time() - self.start_time)
            self.metric_uptime.set_value(f"{e//3600:02d}:{(e%3600)//60:02d}:{e%60:02d}")

    def _update_overlay(self):
        self.fov_overlay.set_stats(self._current_fps, self._current_active)

    def _update_state_label(self):
        if not hasattr(self, "state_lbl"): return
        if self._current_active:
            self.state_lbl.setText(f"◉  {i18n.tr('engaged')}")
            self.state_lbl.setStyleSheet("color: #ffffff; font-weight: 900; letter-spacing: 4px; font-size: 14px;")
        else:
            self.state_lbl.setText(f"○  {i18n.tr('standby')}")
            self.state_lbl.setStyleSheet("color: #666666; font-weight: 900; letter-spacing: 4px; font-size: 14px;")

    def _update_mode_label(self):
        if not hasattr(self, "mode_lbl"): return
        self.mode_lbl.setText(f"{i18n.tr('status_' + config.activation_mode)} // {get_key_name(config.aim_key)}")

    def _update_model_info(self):
        if hasattr(self, "footer_label"):
            self.footer_label.setText(
                f"{i18n.tr('footer_model')} :: {os.path.basename(MODEL_PATH)}    "
                f"{i18n.tr('footer_input')} :: {input_w}×{input_h}    "
                f"{i18n.tr('footer_backend')} :: {session.get_providers()[0]}    "
                f"LIMIT :: {config.fps_limit if config.fps_limit > 0 else 'OFF'}")

    def toggle_ui_visibility(self):
        if self.isVisible(): self.hide()
        else: self.showNormal(); self.raise_(); self.activateWindow()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cw = self.centralWidget()
        if not cw: return
        for attr in ["bg_pattern", "scanline", "corners"]:
            w = getattr(self, attr, None)
            if w: w.setGeometry(cw.rect())

    def _build_qss(self):
        f = self.font_family
        return f"""
        QMainWindow, QWidget {{ background-color: #050505; color: #ffffff;
            font-family: "{f}", "Yu Gothic UI", "Meiryo UI", sans-serif; font-size: 12px; }}
        QGroupBox {{ border: 1px solid #2a2a2a; margin-top: 22px; padding-top: 20px;
            font-weight: 900; letter-spacing: 3px; color: #dcdcdc; background: #080808; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 10px;
            background: #050505; color: #ffffff; letter-spacing: 4px; font-size: 11px; }}
        QLabel {{ color: #cfcfcf; background: transparent; }}
        QComboBox {{ background-color: #0a0a0a; border: 1px solid #3a3a3a;
            padding: 6px 10px; color: #ffffff; min-height: 18px; }}
        QComboBox:hover {{ border: 1px solid #6a6a6a; }}
        QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right;
            width: 22px; border-left: 1px solid #3a3a3a; }}
        QComboBox::down-arrow {{ image: none; width: 0; height: 0;
            border-left: 5px solid transparent; border-right: 5px solid transparent;
            border-top: 6px solid #ffffff; margin-right: 6px; }}
        QComboBox QAbstractItemView {{ background-color: #0a0a0a; color: #ffffff;
            selection-background-color: #ffffff; selection-color: #000000;
            border: 1px solid #5a5a5a; outline: 0px; padding: 4px; }}
        QComboBox QAbstractItemView::item {{ color: #ffffff; background-color: transparent;
            min-height: 26px; padding: 4px 10px; }}
        QComboBox QAbstractItemView::item:hover {{ background-color: #2a2a2a; color: #ffffff; }}
        QComboBox QAbstractItemView::item:selected {{ background-color: #ffffff; color: #000000; }}
        QPushButton {{ background: #0a0a0a; color: #ffffff; border: 1px solid #3a3a3a;
            padding: 10px 16px; font-weight: 900; letter-spacing: 3px; }}
        QPushButton:hover {{ background: #ffffff; color: #000000; border: 1px solid #ffffff; }}
        QPushButton:pressed {{ background: #9a9a9a; color: #000000; }}
        QScrollBar:vertical {{ background: #050505; width: 8px; border: none; }}
        QScrollBar::handle:vertical {{ background: #4a4a4a; min-height: 30px; }}
        QScrollBar::handle:vertical:hover {{ background: #ffffff; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """

    def _add_shadow(self, widget, blur=28, color=QColor(0, 0, 0, 220), dy=4):
        eff = QGraphicsDropShadowEffect(self)
        eff.setBlurRadius(blur); eff.setColor(color); eff.setOffset(0, dy)
        widget.setGraphicsEffect(eff)

    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # ヘッダー
        header = QFrame()
        header.setStyleSheet("QFrame { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #000000, stop:1 #080808); }")
        header.setFixedHeight(88)
        hl = QHBoxLayout(header); hl.setContentsMargins(26, 0, 26, 0); hl.setSpacing(18)
        hl.addWidget(XenarionLogo(56))
        bb = QVBoxLayout(); bb.setSpacing(2)
        self.brand_label = GlitchLabel(i18n.tr("brand")); bb.addWidget(self.brand_label)
        self.brand_sub_label = QLabel(i18n.tr("brand_sub"))
        self.brand_sub_label.setStyleSheet("color: #8a8a8a; font-weight: bold; font-size: 10px; letter-spacing: 6px;")
        bb.addWidget(self.brand_sub_label); hl.addLayout(bb); hl.addStretch()
        self.state_led = StatusLED(size=14, active_color=QColor(255, 255, 255))
        hl.addWidget(self.state_led)
        self.state_lbl = QLabel(f"○  {i18n.tr('standby')}")
        self.state_lbl.setStyleSheet("color: #666666; font-weight: 900; letter-spacing: 4px; font-size: 14px;")
        hl.addWidget(self.state_lbl)

        def sep():
            s = QFrame(); s.setFixedWidth(1); s.setFixedHeight(36)
            s.setStyleSheet("background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #333333, stop:0.5 #666666, stop:1 #333333);")
            return s
        hl.addWidget(sep())
        fb = QVBoxLayout(); fb.setSpacing(0)
        fl = QLabel("FPS"); fl.setStyleSheet("color: #5a5a5a; font-size: 9px; letter-spacing: 4px;")
        fb.addWidget(fl)
        self.header_fps = QLabel("0.0")
        self.header_fps.setStyleSheet("color: #ffffff; font-size: 24px; font-weight: 900; font-family: Consolas, monospace;")
        fb.addWidget(self.header_fps); hl.addLayout(fb)
        hl.addWidget(sep())
        self.mode_lbl = QLabel("HOLD // 右クリック")
        self.mode_lbl.setStyleSheet("color: #ffffff; font-weight: 900; letter-spacing: 2px; font-size: 12px;")
        hl.addWidget(self.mode_lbl); hl.addWidget(sep())
        self.lang_selector = LanguageSelector(config.language)
        self.lang_selector.changed.connect(self._on_language_change)
        hl.addWidget(self.lang_selector); hl.addWidget(sep())
        self.clock_label = QLabel("--:--:--")
        self.clock_label.setStyleSheet("color: #ffffff; font-weight: 900; font-family: Consolas, monospace; font-size: 18px;")
        hl.addWidget(self.clock_label)
        root.addWidget(header)
        root.addWidget(AnimatedAccent())

        body = QWidget(); bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(0)
        root.addWidget(body, stretch=1)

        sidebar = QFrame()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet("background: #060606; border-right: 1px solid #1a1a1a;")
        sl = QVBoxLayout(sidebar); sl.setContentsMargins(0, 22, 0, 22); sl.setSpacing(0)
        nav = QLabel(i18n.tr("nav_label"))
        nav.setStyleSheet("color: #5a5a5a; letter-spacing: 5px; font-weight: 900; font-size: 10px; padding-left: 22px;")
        sl.addWidget(nav); sl.addSpacing(12)
        self.sidebar_group = QButtonGroup(self); self.sidebar_group.setExclusive(True)
        self.side_buttons = []
        for key, icon, idx in [("nav_aim", "crosshair", 0), ("nav_target", "target", 1),
                               ("nav_activation", "lightning", 2), ("nav_system", "gear", 3)]:
            btn = SideBarButton(i18n.tr(key), icon)
            self.sidebar_group.addButton(btn, idx)
            self.side_buttons.append((btn, key)); sl.addWidget(btn)
        self.side_buttons[0][0].setChecked(True)
        self.sidebar_group.idClicked.connect(self._on_side_changed)
        sl.addStretch()
        vb = QVBoxLayout(); vb.setSpacing(2); vb.setContentsMargins(22, 0, 22, 0)
        v1 = QLabel("v3.7.0"); v1.setStyleSheet("color: #5a5a5a; font-size: 10px; letter-spacing: 4px; font-weight: 900;")
        v2 = QLabel("BUILD // 2025.10"); v2.setStyleSheet("color: #3a3a3a; font-size: 8px; letter-spacing: 2px;")
        vb.addWidget(v1); vb.addWidget(v2); sl.addLayout(vb)
        bl.addWidget(sidebar)

        content = QWidget(); content.setStyleSheet("background: transparent;")
        cl = QVBoxLayout(content); cl.setContentsMargins(22, 18, 22, 18); cl.setSpacing(16)
        cl.addWidget(self._build_dashboard())
        self.stack = QStackedWidget()
        self._build_aim_panel(); self._build_target_panel()
        self._build_activation_panel(); self._build_system_panel()
        cl.addWidget(self.stack, stretch=1)
        bl.addWidget(content, stretch=1)

        footer = QFrame()
        footer.setStyleSheet("background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #0a0a0a, stop:1 #050505); border-top: 1px solid #1e1e1e;")
        footer.setFixedHeight(36)
        fl2 = QHBoxLayout(footer); fl2.setContentsMargins(22, 0, 22, 0); fl2.setSpacing(24)
        self.footer_label = QLabel("")
        self.footer_label.setStyleSheet("color: #6a6a6a; font-size: 10px; letter-spacing: 1px; font-family: Consolas, monospace;")
        fl2.addWidget(self.footer_label); fl2.addStretch()
        self.hotkey_label = QLabel(i18n.tr("hotkey_hint"))
        self.hotkey_label.setStyleSheet("color: #ffffff; font-size: 10px; letter-spacing: 2px; font-weight: 900;")
        fl2.addWidget(self.hotkey_label); root.addWidget(footer)

        for w, k in [(self.brand_label, "brand"), (self.brand_sub_label, "brand_sub"),
                     (self.hotkey_label, "hotkey_hint")] + self.side_buttons:
            self._i18n_items.append((w, k))

        self.bg_pattern = BackgroundPattern(central)
        self.bg_pattern.setGeometry(central.rect()); self.bg_pattern.lower()
        self.scanline = ScanlineOverlay(central)
        self.scanline.setGeometry(central.rect()); self.scanline.raise_()
        self.corners = CornerBracket(central)
        self.corners.setGeometry(central.rect()); self.corners.raise_()

        self._update_mode_label(); self._update_model_info()
        self.metric_fps.set_sub(i18n.tr("dash_fps_unit"))
        self.metric_lat.set_sub(i18n.tr("dash_latency_unit"))
        self.metric_dets.set_sub(i18n.tr("dash_targets_unit"))
        self.metric_uptime.set_sub(i18n.tr("dash_session_unit"))

    def _build_dashboard(self):
        dash = AngledCard(cut=12)
        dl = QHBoxLayout(dash); dl.setContentsMargins(20, 12, 20, 12); dl.setSpacing(28)
        self.metric_fps = MetricCard(i18n.tr("dash_fps"), "0.0", "crosshair", "")
        self.metric_lat = MetricCard(i18n.tr("dash_latency"), "0.0 MS", "lightning", "")
        self.metric_dets = MetricCard(i18n.tr("dash_targets"), "0", "target", "")
        self.metric_uptime = MetricCard(i18n.tr("dash_session"), "00:00:00", "gear", "")
        for key, m in [("dash_fps", self.metric_fps), ("dash_latency", self.metric_lat),
                       ("dash_targets", self.metric_dets), ("dash_session", self.metric_uptime)]:
            self._i18n_items.append((m, key)); dl.addWidget(m, stretch=1)
        return dash

    def _on_side_changed(self, idx): self.stack.setCurrentIndex(idx)

    def _make_panel_card(self):
        card = AngledCard(cut=10)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
        inner = QWidget(); layout = QVBoxLayout(inner)
        layout.setContentsMargins(26, 22, 26, 26); layout.setSpacing(18)
        scroll.setWidget(inner)
        outer = QVBoxLayout(card); outer.setContentsMargins(0, 0, 0, 0); outer.addWidget(scroll)
        return card, layout

    def _group(self, key):
        gb = QGroupBox(f"▰▰  {i18n.tr(key)}"); self._i18n_items.append((gb, key)); return gb

    def _label(self, key):
        lbl = QLabel(i18n.tr(key)); lbl.setStyleSheet("letter-spacing: 2px; color: #a5a5a5;")
        self._i18n_items.append((lbl, key)); return lbl

    def _build_aim_panel(self):
        card, l = self._make_panel_card()
        g1 = self._group("section_tracking"); gl1 = QGridLayout(g1)
        gl1.setVerticalSpacing(10); gl1.setHorizontalSpacing(16)
        self._add_slider(gl1, 0, "lbl_track_strength", 0.01, 0.90, config.track_strength, 0.01, self.upd_track, key="track_strength")
        self._add_slider(gl1, 1, "lbl_track_x", 0.0, 2.0, config.track_x, 0.05, self.upd_tx, key="track_x")
        self._add_slider(gl1, 2, "lbl_track_y", 0.0, 2.0, config.track_y, 0.05, self.upd_ty, key="track_y")
        self._add_slider(gl1, 3, "lbl_smoothing", 0.0, 0.99, config.smoothing, 0.01, self.upd_smooth, key="smoothing")
        self._add_slider(gl1, 4, "lbl_deadzone", 0.0, 20.0, config.deadzone_px, 0.5, self.upd_deadzone, key="deadzone_px")
        l.addWidget(g1)
        g2 = self._group("section_anti_recoil"); gl2 = QGridLayout(g2)
        gl2.setVerticalSpacing(10); gl2.setHorizontalSpacing(16)
        self._add_slider(gl2, 0, "lbl_recoil", 0.0, 5.0, config.recoil_compensation, 0.1, self.upd_recoil, key="recoil_compensation")
        l.addWidget(g2); l.addStretch(); self.stack.addWidget(card)

    def _build_target_panel(self):
        card, l = self._make_panel_card()
        g1 = self._group("section_targeting"); gl1 = QGridLayout(g1)
        gl1.setVerticalSpacing(10); gl1.setHorizontalSpacing(16)
        self._add_slider(gl1, 0, "lbl_head_offset", -3.0, 10.0, config.head_offset_y, 0.1, self.upd_offset, key="head_offset_y")
        self._add_slider(gl1, 1, "lbl_fov_radius", 40, 640, config.fov_radius, 1, self.upd_fov, integer=True, key="fov_radius")
        self._add_slider(gl1, 2, "lbl_confidence", 0.10, 0.90, config.conf_threshold, 0.01, self.upd_conf, key="conf_threshold")
        l.addWidget(g1)
        g2 = self._group("section_lock"); gl2 = QGridLayout(g2)
        gl2.setVerticalSpacing(10); gl2.setHorizontalSpacing(16)
        self._add_slider(gl2, 0, "lbl_lock_frames", 0, 60, config.lock_frames, 1, self.upd_lock_f, integer=True, key="lock_frames")
        self._add_slider(gl2, 1, "lbl_iou", 0.1, 0.9, config.lock_iou_threshold, 0.05, self.upd_lock_iou, key="lock_iou_threshold")
        l.addWidget(g2)
        row = QHBoxLayout(); row.setSpacing(12)
        self.toggle_fov_lbl = self._label("lbl_ignore_fov")
        row.addWidget(self.toggle_fov_lbl); row.addStretch()
        self.toggle_fov = NeoToggle(config.fov_filter_enabled)
        self.toggle_fov.toggled.connect(lambda v: setattr(config, "fov_filter_enabled", v))
        row.addWidget(self.toggle_fov); l.addLayout(row); l.addStretch()
        self.stack.addWidget(card)

    def _build_activation_panel(self):
        card, l = self._make_panel_card()
        g1 = self._group("section_activation"); gl1 = QVBoxLayout(g1)
        gl1.setContentsMargins(16, 10, 16, 16); gl1.setSpacing(12)
        d = self._label("lbl_activation_desc"); d.setStyleSheet("color: #7a7a7a; letter-spacing: 2px;")
        gl1.addWidget(d)
        self.segment = SegmentButton([("hold", i18n.tr("mode_hold_btn")),
                                      ("toggle", i18n.tr("mode_toggle_btn")),
                                      ("always", i18n.tr("mode_always_btn"))],
                                     config.activation_mode)
        self.segment.changed.connect(self.on_mode_change); gl1.addWidget(self.segment)
        l.addWidget(g1)
        g2 = self._group("section_keybind"); gl2 = QVBoxLayout(g2)
        gl2.setContentsMargins(16, 10, 16, 16); gl2.setSpacing(12)
        kd = self._label("lbl_keybind_desc"); kd.setStyleSheet("color: #7a7a7a; letter-spacing: 2px;")
        kd.setWordWrap(True); gl2.addWidget(kd)
        kr = QHBoxLayout(); kr.setSpacing(12)
        kr.addWidget(self._label("lbl_aim_key")); kr.addStretch()
        self.key_binder = KeyBinderWidget(config.aim_key)
        self.key_binder.keyChanged.connect(self.on_key_changed); kr.addWidget(self.key_binder)
        gl2.addLayout(kr); l.addWidget(g2)
        info = self._label("info_all"); info.setStyleSheet("color: #6a6a6a; letter-spacing: 1px; font-size: 11px;")
        l.addWidget(info); l.addStretch(); self.stack.addWidget(card)

    def _build_system_panel(self):
        card, l = self._make_panel_card()
        g1 = self._group("section_system"); gl1 = QGridLayout(g1)
        gl1.setVerticalSpacing(12); gl1.setHorizontalSpacing(16)
        gl1.addWidget(self._label("lbl_capture_size"), 0, 0)
        self.cb_cap = QComboBox(); self.cb_cap.addItems(["320", "480", "640", "960"])
        self.cb_cap.setCurrentText(str(config.capture_size))
        self.cb_cap.currentTextChanged.connect(self.upd_cap)
        apply_combo_style(self.cb_cap); gl1.addWidget(self.cb_cap, 0, 1)
        gl1.addWidget(self._label("lbl_polling_rate"), 1, 0)
        self.cb_poll = QComboBox()
        self.cb_poll.addItems(["125 Hz", "500 Hz", "1000 Hz", "2000 Hz", "4000 Hz", "8000 Hz", "10000 Hz"])
        self.cb_poll.setCurrentText(f"{config.polling_rate} Hz")
        self.cb_poll.currentTextChanged.connect(self.upd_poll)
        apply_combo_style(self.cb_poll); gl1.addWidget(self.cb_poll, 1, 1)
        gl1.addWidget(self._label("lbl_fps_limit"), 2, 0)
        self.cb_fps_limit = QComboBox()
        self.cb_fps_limit.addItems(["OFF", "60", "120", "144", "165", "240", "300", "500"])
        self.cb_fps_limit.setCurrentText("OFF" if config.fps_limit == 0 else str(config.fps_limit))
        self.cb_fps_limit.currentTextChanged.connect(self.upd_fps_limit)
        apply_combo_style(self.cb_fps_limit); gl1.addWidget(self.cb_fps_limit, 2, 1)
        l.addWidget(g1)

        gp_box = self._group("section_profile")
        gp = QVBoxLayout(gp_box); gp.setContentsMargins(16, 10, 16, 16); gp.setSpacing(12)
        gp.addWidget(self._label("lbl_profile_name"))
        self.cb_profile = QComboBox(); self.cb_profile.setEditable(True)
        self.cb_profile.addItems(list_profiles())
        self.cb_profile.setCurrentText(config.active_profile or "")
        apply_combo_style(self.cb_profile); gp.addWidget(self.cb_profile)
        brow = QHBoxLayout(); brow.setSpacing(8)
        for key, fn in [("btn_profile_save", self.on_profile_save),
                        ("btn_profile_load", self.on_profile_load),
                        ("btn_profile_delete", self.on_profile_delete)]:
            b = QPushButton(i18n.tr(key)); self._i18n_items.append((b, key))
            b.clicked.connect(fn); brow.addWidget(b)
        gp.addLayout(brow); l.addWidget(gp_box)

        row = QHBoxLayout(); row.setSpacing(12)
        row.addWidget(self._label("lbl_debug")); row.addStretch()
        self.toggle_debug = NeoToggle(config.show_debug)
        self.toggle_debug.toggled.connect(lambda v: setattr(config, "show_debug", v))
        row.addWidget(self.toggle_debug); l.addLayout(row)

        b = QPushButton(i18n.tr("btn_save")); self._i18n_items.append((b, "btn_save"))
        b.clicked.connect(self.on_save_settings); l.addWidget(b)
        l.addStretch(); self.stack.addWidget(card)

    def _add_slider(self, grid, row, key_label, mn, mx, default, step, cb, integer=False, key=None):
        grid.addWidget(self._label(key_label), row, 0)
        slider = NeoSlider(mn, mx, default, step, integer)
        val = ValueLabel(f"{int(default) if integer else default:.2f}")
        def _cb(v):
            val.setText(f"{int(v)}" if integer else f"{v:.2f}"); cb(v)
        slider.valueChanged.connect(_cb)
        grid.addWidget(slider, row, 1); grid.addWidget(val, row, 2)
        grid.setColumnStretch(1, 1)
        if key: self._slider_refs[key] = (slider, val, integer)

    # --- コールバック ---
    def upd_track(self, v): config.track_strength = v
    def upd_tx(self, v): config.track_x = v
    def upd_ty(self, v): config.track_y = v
    def upd_smooth(self, v): config.smoothing = v
    def upd_deadzone(self, v): config.deadzone_px = v
    def upd_recoil(self, v): config.recoil_compensation = v
    def upd_offset(self, v): config.head_offset_y = v
    def upd_conf(self, v): config.conf_threshold = v
    def upd_lock_f(self, v): config.lock_frames = int(v)
    def upd_lock_iou(self, v): config.lock_iou_threshold = v
    def upd_fov(self, v):
        config.fov_radius = int(v); self.fov_overlay.update()
    def upd_poll(self, txt):
        try: config.polling_rate = int(txt.split()[0])
        except ValueError: pass
    def upd_fps_limit(self, txt):
        config.fps_limit = 0 if txt == "OFF" else int(txt)
        self._update_model_info()
    def upd_cap(self, txt):
        new_size = int(txt)
        if new_size == config.capture_size: return
        name = MODEL_MAP.get(new_size)
        if not name:
            self.cb_cap.blockSignals(True); self.cb_cap.setCurrentText(str(config.capture_size))
            self.cb_cap.blockSignals(False); return
        path = os.path.join(BASE_DIR, name)
        if not os.path.isfile(path):
            self.cb_cap.blockSignals(True); self.cb_cap.setCurrentText(str(config.capture_size))
            self.cb_cap.blockSignals(False); return
        self._switch_model_and_size(new_size, path)
    def on_mode_change(self, mode): config.activation_mode = mode; self._update_mode_label()
    def on_key_changed(self, vk): config.aim_key = vk; self._update_mode_label()
    def on_save_settings(self): config.save(); print("[設定] 保存")
    def on_profile_save(self):
        name = self.cb_profile.currentText().strip()
        if name and save_profile(name):
            config.active_profile = name; self._refresh_profiles(name)
    def on_profile_load(self):
        name = self.cb_profile.currentText().strip()
        if name and load_profile(name):
            config.active_profile = name; self._apply_loaded_config(); self._refresh_profiles(name)
    def on_profile_delete(self):
        name = self.cb_profile.currentText().strip()
        if name and delete_profile(name): self._refresh_profiles()
    def _refresh_profiles(self, select=None):
        self.cb_profile.blockSignals(True); self.cb_profile.clear()
        self.cb_profile.addItems(list_profiles())
        if select: self.cb_profile.setCurrentText(select)
        self.cb_profile.blockSignals(False)

    def _apply_loaded_config(self):
        for key, (s, v, integer) in self._slider_refs.items():
            if hasattr(config, key):
                val = getattr(config, key); s.set_value(val)
                v.setText(f"{int(val)}" if integer else f"{val:.2f}")
        self.toggle_fov.setChecked(config.fov_filter_enabled)
        self.toggle_debug.setChecked(config.show_debug)
        self.cb_cap.setCurrentText(str(config.capture_size))
        self.cb_poll.setCurrentText(f"{config.polling_rate} Hz")
        self.cb_fps_limit.setCurrentText("OFF" if config.fps_limit == 0 else str(config.fps_limit))
        self.segment._current = config.activation_mode
        self.segment._anim_offset = float(self.segment._current_index())
        self.segment.update()
        self.key_binder.set_key(config.aim_key)
        i18n.set_lang(config.language); self.lang_selector.set_lang(config.language)
        self._apply_language(); self._update_model_info(); self.fov_overlay.update()
        if config.capture_size != input_w:
            path = os.path.join(BASE_DIR, MODEL_MAP.get(config.capture_size, ""))
            if os.path.isfile(path): self._switch_model_and_size(config.capture_size, path)

    def _switch_model_and_size(self, new_size, model_path):
        global session, input_name, input_w, input_h, MODEL_PATH
        try:
            ns = create_session(model_path); im = ns.get_inputs()[0]
            nw = im.shape[3] if isinstance(im.shape[3], int) else new_size
            nh = im.shape[2] if isinstance(im.shape[2], int) else new_size
        except Exception as e:
            print(f"[エラー] モデル読み込み失敗: {e}"); return False
        self.worker.stop()
        session, input_name = ns, im.name
        input_w, input_h = nw, nh; MODEL_PATH = model_path
        config.capture_size = new_size
        config.fov_radius = min(config.fov_radius, new_size // 2)
        self.worker = InferenceWorker()
        self.worker.state_ready.connect(self.on_state)
        self.worker.stats_ready.connect(self.on_stats)
        self.worker.start()
        self.fov_overlay.update(); self._update_model_info()
        return True

    def on_state(self, dets, target, active):
        self._current_active = active
        self.state_led.set_active(active); self._update_state_label()

    def on_stats(self, stats):
        self._current_fps = stats["fps"]
        self.header_fps.setText(f"{stats['fps']:.1f}")
        self.metric_fps.set_value(f"{stats['fps']:.1f}")
        self.metric_lat.set_value(f"{stats['pre_ms']+stats['inf_ms']+stats['post_ms']:.1f} MS")
        self.metric_dets.set_value(str(stats["dets"]))

    def closeEvent(self, event):
        config.save(); self.hotkey.stop(); self.worker.stop()
        self.fov_overlay.close()
        if _timer_res_raised:
            try: _winmm.timeEndPeriod(1)
            except Exception: pass
        event.accept()


# ============================================================
# エントリポイント（ライセンス検証付き）
# ============================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    user32 = ctypes.windll.user32
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)

    # ========== ライセンス検証 ==========
    cache = _load_cache()
    need_dialog = True

    if cache.get("key"):
        res = validate_license(cache["key"])
        if res.get("valid"):
            need_dialog = False
            tier = res.get("tier_name", "?")
            if res.get("offline"):
                print(f"[ライセンス] ✓ {tier} (オフライン猶予 残り{res['offline_grace_days']}日)")
            else:
                print(f"[ライセンス] ✓ {tier}")

    if need_dialog:
        dlg = LicenseDialog()
        if dlg.exec() != QDialog.DialogCode.Accepted:
            print("[ライセンス] 未認証で終了")
            sys.exit(0)

    families = load_fonts()
    font_family = families[0] if families else "Yu Gothic UI"
    print(f"[フォント] 使用: {font_family}")

    window = MainWindow(font_family)
    window.show()
    sys.exit(app.exec())