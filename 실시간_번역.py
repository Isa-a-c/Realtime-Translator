# ©12211326 장대현 (인하대학교) eosl1212@inha.edu
# 무단 수정 및 배포를 금합니다.
# 버전 1.0.2

import json
import base64
import threading
import time
import websocket
import numpy as np
import pyaudiowpatch as pyaudio
import sounddevice as sd
import pystray
import customtkinter as ctk
from PIL import Image, ImageDraw
from pathlib import Path
from scipy.signal import resample_poly
from math import gcd
import requests
import openai

FIREBASE_API_KEY = "AIzaSyDyHSTKmUMoAUOiooycyhlXf7Xj-Lkv2SE"
FIREBASE_AUTH_URL = "https://identitytoolkit.googleapis.com/v1/accounts"
FIREBASE_REFRESH_URL = "https://securetoken.googleapis.com/v1/token"

# ───────────── Firebase 인증 ─────────────
def firebase_login(email, password):
    url = f"{FIREBASE_AUTH_URL}:signInWithPassword?key={FIREBASE_API_KEY}"
    res = requests.post(url, json={"email": email, "password": password, "returnSecureToken": True})
    return res.json()

def firebase_signup(email, password):
    url = f"{FIREBASE_AUTH_URL}:signUp?key={FIREBASE_API_KEY}"
    res = requests.post(url, json={"email": email, "password": password, "returnSecureToken": True})
    return res.json()

def firebase_refresh_token(refresh_token):
    """토큰 갱신"""
    res = requests.post(
        f"{FIREBASE_REFRESH_URL}?key={FIREBASE_API_KEY}",
        data={"grant_type": "refresh_token", "refresh_token": refresh_token}
    )
    data = res.json()
    return data.get("id_token"), data.get("refresh_token")

FIRESTORE_URL = "https://firestore.googleapis.com/v1/projects/testserver-8be07/databases/(default)/documents"

def check_approved(uid, id_token):
    url = f"{FIRESTORE_URL}/users/{uid}"
    res = requests.get(url, headers={"Authorization": f"Bearer {id_token}"})
    data = res.json()
    if "fields" not in data:
        return False
    return data["fields"].get("approved", {}).get("booleanValue", False)

def check_duplicate_student_id(student_id, id_token):
    url = f"{FIRESTORE_URL}:runQuery"
    body = {
        "structuredQuery": {
            "from": [{"collectionId": "users"}],
            "where": {
                "fieldFilter": {
                    "field": {"fieldPath": "student_id"},
                    "op": "EQUAL",
                    "value": {"stringValue": student_id}
                }
            },
            "limit": 1
        }
    }
    res = requests.post(url, json=body, headers={"Authorization": f"Bearer {id_token}"})
    data = res.json()
    return any("document" in item for item in data)

def create_user_doc(uid, email, name, student_id, id_token):
    url = f"{FIRESTORE_URL}/users/{uid}"
    body = {
        "fields": {
            "email": {"stringValue": email},
            "name": {"stringValue": name},
            "student_id": {"stringValue": student_id},
            "approved": {"booleanValue": False}
        }
    }
    requests.patch(url, json=body, headers={"Authorization": f"Bearer {id_token}"})

# ───────────── 앱 정보 ─────────────
APP_NAME = "실시간 번역기 v1.0.2  |  장대현"
APP_VERSION = "1.0.2"
APP_AUTHOR = "장대현 (인하대학교)"
APP_EMAIL = "eosl1212@inha.edu"

CONFIG_FILE = Path("config.json")
REALTIME_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
TARGET_RATE = 24000
MAX_LABELS = 200   # 메모리 누수 방지: 최대 라벨 수

LANGUAGES = {
    "자동 감지": None,
    "한국어": "ko",
    "영어": "en",
    "일본어": "ja",
    "중국어": "zh",
    "스페인어": "es",
    "프랑스어": "fr",
    "독일어": "de",
}

OUTPUT_LANGUAGES = {
    "한국어": "Korean",
    "영어": "English",
    "일본어": "Japanese",
    "중국어": "Chinese",
    "스페인어": "Spanish",
    "프랑스어": "French",
    "독일어": "German",
}

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f)

def get_input_devices():
    devices = []
    for i, d in enumerate(sd.query_devices()):
        if d['max_input_channels'] > 0:
            devices.append({"index": i, "name": d['name']})
    return devices

def get_loopback_devices():
    devices = []
    try:
        pa = pyaudio.PyAudio()
        for i in range(pa.get_device_count()):
            try:
                info = pa.get_device_info_by_index(i)
                if info.get("isLoopbackDevice", False) and info.get("maxInputChannels", 0) > 0:
                    devices.append({
                        "index": i,
                        "name": info['name'],
                        "sampleRate": int(info['defaultSampleRate']),
                        "channels": int(info['maxInputChannels'])
                    })
            except Exception:
                continue
        pa.terminate()
    except Exception:
        pass
    return devices

def create_tray_icon():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill=(59, 130, 246))
    draw.text((18, 18), "T", fill="white")
    return img

# ───────────── 로그인 화면 ─────────────
class LoginWindow(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title("실시간 번역기 - 로그인")
        self.resizable(False, False)
        self.update_idletasks()
        w, h = 480, 580
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.configure(fg_color="#111111")
        self.user_token = None
        self.refresh_token = None
        self._build_ui()

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="#1a1a1a", corner_radius=0, height=90)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(header, text="🌐  실시간 번역기",
                     font=ctk.CTkFont(size=26, weight="bold"), text_color="white"
                     ).pack(side="left", padx=24, pady=28)

        self.tab_var = ctk.StringVar(value="로그인")
        tab_frame = ctk.CTkFrame(self, fg_color="transparent")
        tab_frame.pack(fill="x", padx=20, pady=(20, 0))
        ctk.CTkSegmentedButton(
            tab_frame, values=["로그인", "회원가입"],
            variable=self.tab_var, height=44,
            font=ctk.CTkFont(size=17),
            selected_color="#2563eb", selected_hover_color="#1d4ed8",
            unselected_color="#252525", unselected_hover_color="#333333",
            command=self._on_tab_change
        ).pack(fill="x")

        form = ctk.CTkFrame(self, fg_color="#1c1c1c", corner_radius=12)
        form.pack(fill="x", padx=20, pady=(12, 0))

        config = load_config()

        ctk.CTkLabel(form, text="이메일", font=ctk.CTkFont(size=16),
                     text_color="#888888", anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        self.email_entry = ctk.CTkEntry(form, placeholder_text="example@email.com",
                                         height=44, corner_radius=8, font=ctk.CTkFont(size=16),
                                         fg_color="#252525", border_color="#333333")
        self.email_entry.pack(fill="x", padx=16, pady=(0, 4))
        if config.get("saved_email"):
            self.email_entry.insert(0, config["saved_email"])

        self.save_email_var = ctk.BooleanVar(value=bool(config.get("saved_email")))
        def toggle_save_email():
            cfg = load_config()
            cfg["saved_email"] = self.email_entry.get().strip() if self.save_email_var.get() else ""
            save_config(cfg)
        ctk.CTkCheckBox(form, text="이메일 저장",
                         variable=self.save_email_var,
                         command=toggle_save_email,
                         font=ctk.CTkFont(size=12), text_color="#888888",
                         fg_color="#2563eb", hover_color="#1d4ed8",
                         checkmark_color="white"
                         ).pack(anchor="w", padx=16, pady=(0, 8))

        ctk.CTkLabel(form, text="비밀번호", font=ctk.CTkFont(size=16),
                     text_color="#888888", anchor="w").pack(fill="x", padx=16, pady=(0, 2))
        self.pw_entry = ctk.CTkEntry(form, placeholder_text="비밀번호", show="*",
                                      height=44, corner_radius=8, font=ctk.CTkFont(size=16),
                                      fg_color="#252525", border_color="#333333")
        self.pw_entry.pack(fill="x", padx=16, pady=(0, 4))
        if config.get("saved_password"):
            self.pw_entry.insert(0, config["saved_password"])

        self.save_pw_var = ctk.BooleanVar(value=bool(config.get("saved_password")))
        def toggle_save_pw():
            cfg = load_config()
            cfg["saved_password"] = self.pw_entry.get().strip() if self.save_pw_var.get() else ""
            save_config(cfg)
        ctk.CTkCheckBox(form, text="비밀번호 저장",
                         variable=self.save_pw_var,
                         command=toggle_save_pw,
                         font=ctk.CTkFont(size=12), text_color="#888888",
                         fg_color="#2563eb", hover_color="#1d4ed8",
                         checkmark_color="white"
                         ).pack(anchor="w", padx=16, pady=(0, 10))

        # 엔터키 바인딩
        self.email_entry.bind("<Return>", lambda e: self.pw_entry.focus())
        self.pw_entry.bind("<Return>", lambda e: self._action())

        self.extra_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.extra_frame.pack(fill="x")

        ctk.CTkLabel(self.extra_frame, text="이름", font=ctk.CTkFont(size=14),
                     text_color="#888888", anchor="w").pack(fill="x", padx=16, pady=(0, 2))
        self.name_entry = ctk.CTkEntry(self.extra_frame, placeholder_text="홍길동",
                                        height=40, corner_radius=8, font=ctk.CTkFont(size=14),
                                        fg_color="#252525", border_color="#333333")
        self.name_entry.pack(fill="x", padx=16, pady=(0, 10))

        ctk.CTkLabel(self.extra_frame, text="학번", font=ctk.CTkFont(size=14),
                     text_color="#888888", anchor="w").pack(fill="x", padx=16, pady=(0, 2))
        self.student_id_entry = ctk.CTkEntry(self.extra_frame, placeholder_text="12345678",
                                              height=40, corner_radius=8, font=ctk.CTkFont(size=14),
                                              fg_color="#252525", border_color="#333333")
        self.student_id_entry.pack(fill="x", padx=16, pady=(0, 14))
        self.extra_frame.pack_forget()

        self.status_label = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=15), text_color="red")
        self.status_label.pack(pady=(10, 0))

        self.action_btn = ctk.CTkButton(self, text="로그인",
                                         font=ctk.CTkFont(size=20, weight="bold"),
                                         height=58, corner_radius=12,
                                         fg_color="#2563eb", hover_color="#1d4ed8",
                                         command=self._action)
        self.action_btn.pack(fill="x", padx=20, pady=(10, 0))

        ctk.CTkLabel(self, text=f"{APP_AUTHOR}  ·  {APP_EMAIL}",
                     font=ctk.CTkFont(size=13), text_color="#444444").pack(pady=(20, 0))

    def _on_tab_change(self, value):
        self.action_btn.configure(text=value)
        self.status_label.configure(text="")
        if value == "회원가입":
            self.extra_frame.pack(fill="x")
            self.geometry("480x720")
        else:
            self.extra_frame.pack_forget()
            self.geometry("480x580")

    def _action(self):
        email = self.email_entry.get().strip()
        password = self.pw_entry.get().strip()
        if not email or not password:
            self.status_label.configure(text="이메일과 비밀번호를 입력해주세요", text_color="red")
            return
        self.action_btn.configure(state="disabled", text="처리 중...")
        self.status_label.configure(text="")

        def do_auth():
            try:
                if self.tab_var.get() == "로그인":
                    result = firebase_login(email, password)
                else:
                    result = firebase_signup(email, password)

                if "idToken" in result:
                    id_token = result["idToken"]
                    uid = result["localId"]
                    refresh_tok = result.get("refreshToken")

                    if self.tab_var.get() == "회원가입":
                        name = self.name_entry.get().strip()
                        student_id = self.student_id_entry.get().strip()
                        if not name or not student_id:
                            self.after(0, lambda: self.status_label.configure(
                                text="이름과 학번을 입력해주세요", text_color="red"))
                            self.after(0, lambda: self.action_btn.configure(
                                state="normal", text="회원가입"))
                            return
                        if check_duplicate_student_id(student_id, id_token):
                            self.after(0, lambda: self.status_label.configure(
                                text="이미 가입된 학번입니다", text_color="red"))
                            self.after(0, lambda: self.action_btn.configure(
                                state="normal", text="회원가입"))
                            return
                        create_user_doc(uid, result.get("email", ""), name, student_id, id_token)
                        self.after(0, lambda: self.status_label.configure(
                            text="가입 완료! 관리자 승인 후 이용 가능합니다", text_color="green"))
                        self.after(0, lambda: self.action_btn.configure(
                            state="normal", text="회원가입"))
                    else:
                        if check_approved(uid, id_token):
                            self.user_token = id_token
                            self.refresh_token = refresh_tok
                            self.after(0, self._on_success)
                        else:
                            self.after(0, lambda: self.status_label.configure(
                                text="관리자 승인 대기 중입니다.", text_color="red"))
                            self.after(0, lambda: self.action_btn.configure(
                                state="normal", text="로그인"))
                else:
                    error = result.get("error", {}).get("message", "알 수 없는 오류")
                    error_map = {
                        "EMAIL_NOT_FOUND": "등록되지 않은 이메일입니다",
                        "INVALID_PASSWORD": "비밀번호가 틀렸습니다",
                        "INVALID_LOGIN_CREDENTIALS": "이메일 또는 비밀번호가 틀렸습니다",
                        "EMAIL_EXISTS": "이미 사용 중인 이메일입니다",
                        "WEAK_PASSWORD : Password should be at least 6 characters": "비밀번호는 6자 이상이어야 합니다",
                    }
                    msg = error_map.get(error, f"오류: {error}")
                    self.after(0, lambda: self.status_label.configure(text=msg, text_color="red"))
                    self.after(0, lambda: self.action_btn.configure(
                        state="normal", text=self.tab_var.get()))
            except Exception:
                self.after(0, lambda: self.status_label.configure(text="네트워크 오류", text_color="red"))
                self.after(0, lambda: self.action_btn.configure(
                    state="normal", text=self.tab_var.get()))

        threading.Thread(target=do_auth, daemon=True).start()

    def _on_success(self):
        self.quit()

# ───────────── 오버레이 자막 창 ─────────────
class OverlayWindow(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__()
        self._app = app
        self.title("")
        self.geometry("800x600+300+400")
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.85)
        self.overrideredirect(True)
        self.configure(fg_color="#1a1a1a")

        self.MAX_ITEMS = 50   # 메모리 누수 방지
        self._resize_data = None

        self.drag_bar = ctk.CTkFrame(self, height=44, fg_color="#2a2a2a", corner_radius=0)
        self.drag_bar.pack(fill="x")
        self.drag_bar.pack_propagate(False)
        self.drag_bar.configure(cursor="fleur")
        self.drag_bar.bind("<ButtonPress-1>", self._start_drag)
        self.drag_bar.bind("<B1-Motion>", self._on_drag)

        ctk.CTkButton(self.drag_bar, text="✕", width=36, height=28,
                      fg_color="transparent", hover_color="#e74c3c",
                      text_color="#888888", font=ctk.CTkFont(size=15),
                      command=self._close).pack(side="right", padx=6, pady=4)

        ctk.CTkLabel(self.drag_bar, text="글자 크기",
                     font=ctk.CTkFont(size=13), text_color="#888888"
                     ).pack(side="right", padx=(0, 4), pady=4)

        self.font_slider = ctk.CTkSlider(self.drag_bar, from_=12, to=32,
                                          width=180, height=16, number_of_steps=20,
                                          command=self._on_font_change)
        self.font_slider.set(22)  # 중간값
        self.font_slider.pack(side="right", padx=(0, 8), pady=4)

        title_label = ctk.CTkLabel(self.drag_bar, text="☰  실시간 번역",
                                    font=ctk.CTkFont(size=15), text_color="#888888", cursor="fleur")
        title_label.pack(side="left", padx=12, pady=4)
        title_label.bind("<ButtonPress-1>", self._start_drag)
        title_label.bind("<B1-Motion>", self._on_drag)

        self.scroll_frame = ctk.CTkScrollableFrame(self, fg_color="#1a1a1a", corner_radius=0)
        self.scroll_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        self.placeholder = ctk.CTkLabel(self.scroll_frame, text="번역 대기 중...",
                                         font=ctk.CTkFont(size=14), text_color="#555555")
        self.placeholder.pack(pady=20)

        self.items = []
        self.all_labels = []
        self._add_resize_handles()
        self.bind("<Configure>", self._on_resize_update)

    def _on_font_change(self, value):
        size = int(value)
        wrap = max(200, self.winfo_width() - 60)
        for label, is_translated in self.all_labels:
            try:
                font_size = size + 6 if is_translated else size
                label.configure(
                    font=ctk.CTkFont(size=font_size, weight="bold" if is_translated else "normal"),
                    wraplength=wrap)
            except Exception:
                pass

    def _on_resize_update(self, e):
        new_wrap = max(200, self.winfo_width() - 60)
        for label, _ in self.all_labels:
            try:
                label.configure(wraplength=new_wrap)
            except Exception:
                pass

    def _add_resize_handles(self):
        import tkinter as tk
        S = 6
        cursor_map = {
            "n": "top_side", "s": "bottom_side",
            "w": "left_side", "e": "right_side",
            "nw": "top_left_corner", "ne": "top_right_corner",
            "sw": "bottom_left_corner", "se": "bottom_right_corner"
        }
        handles = [
            ("n",  dict(relx=0,   rely=0,   relwidth=1,  height=S)),
            ("s",  dict(relx=0,   rely=1.0, relwidth=1,  height=S,  y=-S)),
            ("w",  dict(relx=0,   rely=0,   width=S,     relheight=1)),
            ("e",  dict(relx=1.0, rely=0,   width=S,     relheight=1, x=-S)),
            ("nw", dict(relx=0,   rely=0,   width=S*2,   height=S*2)),
            ("ne", dict(relx=1.0, rely=0,   width=S*2,   height=S*2, x=-S*2)),
            ("sw", dict(relx=0,   rely=1.0, width=S*2,   height=S*2, y=-S*2)),
            ("se", dict(relx=1.0, rely=1.0, width=S*2,   height=S*2, x=-S*2, y=-S*2)),
        ]
        for direction, opts in handles:
            h = tk.Frame(self, bg="", cursor=cursor_map[direction])
            h.place(**opts)
            h.bind("<ButtonPress-1>", lambda e, d=direction: self._start_resize(e, d))
            h.bind("<B1-Motion>", self._do_resize)

    def _start_resize(self, e, direction):
        self._resize_data = {
            "x": e.x_root, "y": e.y_root,
            "w": self.winfo_width(), "h": self.winfo_height(),
            "wx": self.winfo_x(), "wy": self.winfo_y(),
            "dir": direction
        }

    def _do_resize(self, e):
        if not self._resize_data:
            return
        d = self._resize_data
        dx = e.x_root - d["x"]
        dy = e.y_root - d["y"]
        x, y, w, h = d["wx"], d["wy"], d["w"], d["h"]
        if "e" in d["dir"]: w = max(400, w + dx)
        if "s" in d["dir"]: h = max(200, h + dy)
        if "w" in d["dir"]:
            w = max(400, w - dx)
            x = d["wx"] + d["w"] - w
        if "n" in d["dir"]:
            h = max(200, h - dy)
            y = d["wy"] + d["h"] - h
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _close(self):
        self._app._stop()
        self.destroy()

    def _start_drag(self, e):
        self._dx = e.x
        self._dy = e.y

    def _on_drag(self, e):
        x = self.winfo_x() + e.x - self._dx
        y = self.winfo_y() + e.y - self._dy
        self.geometry(f"+{x}+{y}")

    def update_text(self, original, translated):
        if self.placeholder:
            self.placeholder.destroy()
            self.placeholder = None

        # 최대 아이템 초과 시 가장 오래된 것 제거 + 라벨도 정리
        if len(self.items) >= self.MAX_ITEMS:
            oldest = self.items.pop()
            oldest.destroy()
            # 해당 카드의 라벨 2개 제거 (메모리 누수 방지)
            if len(self.all_labels) >= 2:
                self.all_labels = self.all_labels[:-2]

        font_size = int(self.font_slider.get())
        card = ctk.CTkFrame(self.scroll_frame, fg_color="#2a2a2a", corner_radius=8)
        wrap = max(200, self.winfo_width() - 60)

        orig_label = ctk.CTkLabel(card, text=original,
                                   font=ctk.CTkFont(size=font_size),
                                   text_color="#aaaaaa", anchor="w",
                                   wraplength=wrap, justify="left")
        orig_label.pack(anchor="w", padx=12, pady=(8, 2))

        trans_label = ctk.CTkLabel(card, text=translated,
                                    font=ctk.CTkFont(size=font_size + 6, weight="bold"),
                                    text_color="white", anchor="w",
                                    wraplength=wrap, justify="left")
        trans_label.pack(anchor="w", padx=12, pady=(0, 8))

        # all_labels 크기 제한
        self.all_labels.insert(0, (orig_label, False))
        self.all_labels.insert(0, (trans_label, True))
        if len(self.all_labels) > MAX_LABELS:
            self.all_labels = self.all_labels[:MAX_LABELS]

        self.items.insert(0, card)
        card.pack(fill="x", pady=3, padx=4)

        for item in self.items[1:]:
            item.pack_forget()
            item.pack(fill="x", pady=3, padx=4)

        self.scroll_frame._parent_canvas.yview_moveto(0.0)

# ───────────── 메인 GUI ─────────────
class App(ctk.CTk):
    def __init__(self, user_token, refresh_token):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(APP_NAME)
        self.resizable(False, False)
        self.update_idletasks()
        w, h = 480, 780
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.ws_app = None
        self.running = False
        self.ws_connected = threading.Event()
        self.overlay = None
        self.tray = None

        # Firebase 토큰 관리
        self._id_token = user_token
        self._refresh_token = refresh_token
        self._token_timer = None
        self._start_token_refresh()

        # 오디오 장치 모니터
        self._last_device_list = []
        self._device_monitor_running = True
        threading.Thread(target=self._monitor_devices, daemon=True).start()

        self.config = load_config()
        self._build_ui()
        self._start_tray()
        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)

    # ── Firebase 토큰 자동 갱신 (55분마다) ──
    def _start_token_refresh(self):
        def refresh():
            while True:
                time.sleep(55 * 60)  # 55분 대기
                try:
                    new_token, new_refresh = firebase_refresh_token(self._refresh_token)
                    if new_token:
                        self._id_token = new_token
                        self._refresh_token = new_refresh
                except Exception:
                    pass
        threading.Thread(target=refresh, daemon=True).start()

    # ── 오디오 장치 변경 감지 ──
    def _monitor_devices(self):
        while self._device_monitor_running:
            time.sleep(5)
            try:
                mode = self.mode_var.get() if hasattr(self, 'mode_var') else None
                if not mode:
                    continue
                if mode == "사용자 마이크":
                    current = get_input_devices()
                else:
                    current = get_loopback_devices()

                current_names = [d['name'] for d in current]
                last_names = [d['name'] for d in self._last_device_list]

                if current_names != last_names:
                    self._last_device_list = current
                    self.after(0, self._refresh_devices)
            except Exception:
                pass

    def _build_ui(self):
        self.configure(fg_color="#111111")

        header = ctk.CTkFrame(self, fg_color="#1a1a1a", corner_radius=0, height=90)
        header.pack(fill="x")
        header.pack_propagate(False)

        left_frame = ctk.CTkFrame(header, fg_color="transparent")
        left_frame.pack(side="left", padx=24, fill="y")
        ctk.CTkLabel(left_frame, text="🌐  실시간 번역기",
                     font=ctk.CTkFont(size=26, weight="bold"), text_color="white"
                     ).pack(anchor="w", pady=(22, 2))

        right_frame = ctk.CTkFrame(header, fg_color="transparent")
        right_frame.pack(side="right", padx=24, fill="y")
        ctk.CTkLabel(right_frame, text=APP_AUTHOR,
                     font=ctk.CTkFont(size=17, weight="bold"), text_color="#cccccc"
                     ).pack(anchor="e", pady=(20, 2))
        ctk.CTkLabel(right_frame, text=APP_EMAIL,
                     font=ctk.CTkFont(size=14), text_color="#555555").pack(anchor="e")

        # API 키
        self._section_label("API 키")
        key_frame = ctk.CTkFrame(self, fg_color="#1c1c1c", corner_radius=12)
        key_frame.pack(fill="x", padx=20, pady=(4, 0))

        ctk.CTkLabel(key_frame, text="OpenAI", font=ctk.CTkFont(size=16),
                     text_color="#888888", anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        openai_row = ctk.CTkFrame(key_frame, fg_color="transparent")
        openai_row.pack(fill="x", padx=16, pady=(0, 10))
        self.openai_entry = ctk.CTkEntry(openai_row, placeholder_text="sk-...", show="*",
                                          height=44, corner_radius=8, font=ctk.CTkFont(size=16),
                                          fg_color="#252525", border_color="#333333")
        self.openai_entry.pack(side="left", fill="x", expand=True)
        if self.config.get("openai"):
            self.openai_entry.insert(0, self.config["openai"])
        self._eye_btn(openai_row, self.openai_entry)

        self.save_keys_var = ctk.BooleanVar(value=bool(self.config.get("openai")))
        ctk.CTkCheckBox(key_frame, text="API 키 저장",
                         variable=self.save_keys_var,
                         command=self._on_save_keys_toggle,
                         font=ctk.CTkFont(size=16), text_color="#888888",
                         checkmark_color="white", fg_color="#2563eb", hover_color="#1d4ed8"
                         ).pack(anchor="w", padx=16, pady=(0, 14))

        # 입력 모드
        self._section_label("입력 모드")
        mode_frame = ctk.CTkFrame(self, fg_color="#1c1c1c", corner_radius=12)
        mode_frame.pack(fill="x", padx=20, pady=(4, 0))
        self.mode_var = ctk.StringVar(value=self.config.get("mode", "사용자 마이크"))
        ctk.CTkSegmentedButton(
            mode_frame, values=["사용자 마이크", "시스템 오디오"],
            variable=self.mode_var, height=44, corner_radius=8,
            font=ctk.CTkFont(size=16),
            selected_color="#2563eb", selected_hover_color="#1d4ed8",
            unselected_color="#252525", unselected_hover_color="#333333",
        ).pack(fill="x", padx=16, pady=14)

        # 오디오 장치
        self._section_label("오디오 장치")
        device_frame = ctk.CTkFrame(self, fg_color="#1c1c1c", corner_radius=12)
        device_frame.pack(fill="x", padx=20, pady=(4, 0))

        device_inner = ctk.CTkFrame(device_frame, fg_color="transparent")
        device_inner.pack(fill="x", padx=16, pady=12)

        self.device_var = ctk.StringVar(value="기본 장치")
        self.device_menu = ctk.CTkOptionMenu(device_inner, values=["기본 장치"],
                                              variable=self.device_var,
                                              height=40, corner_radius=8,
                                              fg_color="#252525", button_color="#2563eb",
                                              font=ctk.CTkFont(size=15))
        self.device_menu.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(device_inner, text="🔄", width=44, height=40,
                      corner_radius=8, fg_color="#252525", hover_color="#333333",
                      font=ctk.CTkFont(size=16),
                      command=self._refresh_devices).pack(side="left", padx=(8, 0))

        # 볼륨 미터
        meter_frame = ctk.CTkFrame(device_frame, fg_color="transparent")
        meter_frame.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(meter_frame, text="입력 레벨",
                     font=ctk.CTkFont(size=12), text_color="#888888"
                     ).pack(side="left", padx=(0, 8))
        self.volume_bar = ctk.CTkProgressBar(meter_frame, height=12, corner_radius=6,
                                              fg_color="#252525", progress_color="#2563eb")
        self.volume_bar.set(0)
        self.volume_bar.pack(side="left", fill="x", expand=True)
        self.volume_label = ctk.CTkLabel(meter_frame, text="0%",
                                          font=ctk.CTkFont(size=12), text_color="#888888", width=36)
        self.volume_label.pack(side="left", padx=(8, 0))

        self._meter_running = False
        self._meter_thread = None

        self.mode_var.trace_add("write", lambda *a: self._refresh_devices())
        self.device_var.trace_add("write", lambda *a: self._start_volume_meter())
        self._refresh_devices()

        # 원문 언어
        self.lang_var = ctk.StringVar(value=self.config.get("language", "자동 감지"))
        self.lang_popup = None

        lang_frame = ctk.CTkFrame(self, fg_color="#1c1c1c", corner_radius=12)
        lang_frame.pack(fill="x", padx=20, pady=(16, 0))

        # 원문 언어 행
        input_row = ctk.CTkFrame(lang_frame, fg_color="transparent")
        input_row.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(input_row, text="원문 언어",
                     font=ctk.CTkFont(size=15, weight="bold"), text_color="#aaaaaa").pack(side="left")
        self.lang_toggle_btn = ctk.CTkButton(
            input_row, text=f"  {self.lang_var.get()}  ▾",
            width=160, height=38, corner_radius=10,
            fg_color="#2563eb", hover_color="#1d4ed8",
            text_color="white", font=ctk.CTkFont(size=15, weight="bold"),
            command=self._toggle_lang)
        self.lang_toggle_btn.pack(side="right")

        # 출력 언어 행
        self.out_lang_var = ctk.StringVar(value=self.config.get("output_language", "한국어"))
        self.out_lang_popup = None

        output_row = ctk.CTkFrame(lang_frame, fg_color="transparent")
        output_row.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(output_row, text="출력 언어",
                     font=ctk.CTkFont(size=15, weight="bold"), text_color="#aaaaaa").pack(side="left")
        self.out_lang_toggle_btn = ctk.CTkButton(
            output_row, text=f"  {self.out_lang_var.get()}  ▾",
            width=160, height=38, corner_radius=10,
            fg_color="#1d4ed8", hover_color="#1e3a8a",
            text_color="white", font=ctk.CTkFont(size=15, weight="bold"),
            command=self._toggle_out_lang)
        self.out_lang_toggle_btn.pack(side="right")

        # 하단
        self.bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.bottom_frame.pack(fill="x", side="bottom", pady=(0, 8))
        ctk.CTkLabel(self.bottom_frame, text=f"v{APP_VERSION}",
                     font=ctk.CTkFont(size=12), text_color="#444444").pack(pady=(0, 8))
        self.start_btn = ctk.CTkButton(self.bottom_frame, text="▶   시작",
                                        font=ctk.CTkFont(size=22, weight="bold"),
                                        height=60, corner_radius=12,
                                        fg_color="#2563eb", hover_color="#1d4ed8",
                                        command=self._toggle)
        self.start_btn.pack(fill="x", padx=20, pady=(0, 8))
        self.status_label = ctk.CTkLabel(self.bottom_frame, text="대기 중",
                                          font=ctk.CTkFont(size=16), text_color="#555555")
        self.status_label.pack(pady=(4, 0))

    def _eye_btn(self, parent, entry):
        def toggle():
            if entry.cget("show") == "*":
                entry.configure(show="")
                btn.configure(text="🙈")
            else:
                entry.configure(show="*")
                btn.configure(text="👁")
        btn = ctk.CTkButton(parent, text="👁", width=36, height=36,
                             fg_color="#252525", hover_color="#333333",
                             corner_radius=8, command=toggle)
        btn.pack(side="left", padx=(6, 0))

    def _section_label(self, text):
        ctk.CTkLabel(self, text=text, font=ctk.CTkFont(size=17, weight="bold"),
                     text_color="#aaaaaa", anchor="w").pack(fill="x", padx=20, pady=(16, 4))

    def _refresh_devices(self):
        mode = self.mode_var.get()
        if mode == "사용자 마이크":
            devices = get_input_devices()
        else:
            devices = get_loopback_devices()
        self._device_list = devices
        self._last_device_list = devices
        names = ["기본 장치"] + [d['name'] for d in devices]
        self.device_menu.configure(values=names)
        self.device_var.set("기본 장치")

    def _start_volume_meter(self):
        self._meter_running = False
        if self._meter_thread and self._meter_thread.is_alive():
            self._meter_thread.join(timeout=1.0)
        self._meter_running = True

        def run_meter():
            selected = self.device_var.get()
            mode = self.mode_var.get()
            try:
                if mode == "사용자 마이크":
                    if selected == "기본 장치" or not hasattr(self, '_device_list'):
                        device_idx = sd.default.device[0]
                    else:
                        match = next((d for d in self._device_list if d['name'] == selected), None)
                        device_idx = match['index'] if match else sd.default.device[0]
                    device_info = sd.query_devices(device_idx, 'input')
                    rate = int(device_info['default_samplerate'])

                    def mic_meter_cb(indata, frames, t, status):
                        if not self._meter_running:
                            raise sd.CallbackStop()
                        vol = float(np.abs(indata).mean())
                        val = min(vol * 20, 1.0)
                        pct = int(val * 100)
                        self.after(0, lambda: self.volume_bar.set(val))
                        self.after(0, lambda: self.volume_label.configure(text=f"{pct}%"))

                    with sd.InputStream(device=device_idx, samplerate=rate,
                                        channels=1, dtype='float32',
                                        blocksize=int(rate * 0.1), callback=mic_meter_cb):
                        while self._meter_running:
                            time.sleep(0.1)
                else:
                    pa = pyaudio.PyAudio()
                    if selected == "기본 장치" or not hasattr(self, '_device_list'):
                        speakers = pa.get_default_wasapi_loopback()
                    else:
                        match = next((d for d in self._device_list if d['name'] == selected), None)
                        speakers = pa.get_device_info_by_index(match['index']) if match else pa.get_default_wasapi_loopback()

                    rate = int(speakers["defaultSampleRate"])
                    channels = int(speakers["maxInputChannels"])
                    device_idx = int(speakers["index"])
                    chunk = int(rate * 0.1)

                    def loop_meter_cb(in_data, frame_count, time_info, status):
                        if not self._meter_running:
                            return (None, pyaudio.paComplete)
                        raw = np.frombuffer(in_data, dtype=np.float32)
                        vol = float(np.abs(raw).mean())
                        val = min(vol * 20, 1.0)
                        pct = int(val * 100)
                        self.after(0, lambda: self.volume_bar.set(val))
                        self.after(0, lambda: self.volume_label.configure(text=f"{pct}%"))
                        return (None, pyaudio.paContinue)

                    stream = pa.open(format=pyaudio.paFloat32, channels=channels,
                                     rate=rate, input=True, input_device_index=device_idx,
                                     frames_per_buffer=chunk, stream_callback=loop_meter_cb)
                    stream.start_stream()
                    while self._meter_running and stream.is_active():
                        time.sleep(0.05)
                    stream.stop_stream()
                    stream.close()
                    pa.terminate()
            except Exception:
                self.after(0, lambda: self.volume_bar.set(0))
                self.after(0, lambda: self.volume_label.configure(text="0%"))

        self._meter_thread = threading.Thread(target=run_meter, daemon=True)
        self._meter_thread.start()

    def _toggle_lang(self):
        if self.lang_popup and self.lang_popup.winfo_exists():
            self.lang_popup.destroy()
            self.lang_popup = None
            return

        btn = self.lang_toggle_btn
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() + btn.winfo_height() + 4

        popup = ctk.CTkToplevel(self)
        popup.overrideredirect(True)
        popup.geometry(f"200x{len(LANGUAGES) * 46 + 8}+{x}+{y}")
        popup.configure(fg_color="#1c1c1c")
        popup.attributes("-topmost", True)
        popup.transient(self)  # 메인 창에 종속 → 같이 움직임
        self.lang_popup = popup

        for lang_name in LANGUAGES:
            ctk.CTkButton(popup, text=lang_name, height=42,
                          fg_color="transparent", hover_color="#2a2a2a",
                          text_color="white", anchor="w", corner_radius=8,
                          font=ctk.CTkFont(size=16),
                          command=lambda l=lang_name: self._select_lang(l)).pack(fill="x", padx=4, pady=2)

    def _toggle_out_lang(self):
        if self.out_lang_popup and self.out_lang_popup.winfo_exists():
            self.out_lang_popup.destroy()
            self.out_lang_popup = None
            return

        btn = self.out_lang_toggle_btn
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() + btn.winfo_height() + 4

        popup = ctk.CTkToplevel(self)
        popup.overrideredirect(True)
        popup.geometry(f"200x{len(OUTPUT_LANGUAGES) * 46 + 8}+{x}+{y}")
        popup.configure(fg_color="#1c1c1c")
        popup.attributes("-topmost", True)
        popup.transient(self)
        self.out_lang_popup = popup

        for lang_name in OUTPUT_LANGUAGES:
            ctk.CTkButton(
                popup, text=lang_name, height=42,
                fg_color="transparent", hover_color="#2a2a2a",
                text_color="white", anchor="w", corner_radius=8,
                font=ctk.CTkFont(size=16),
                command=lambda l=lang_name: self._select_out_lang(l)
            ).pack(fill="x", padx=4, pady=2)

        def close_if_outside(e):
            try:
                px, py = popup.winfo_rootx(), popup.winfo_rooty()
                pw, ph = popup.winfo_width(), popup.winfo_height()
                if not (px <= e.x_root <= px+pw and py <= e.y_root <= py+ph):
                    popup.destroy()
                    self.out_lang_popup = None
                    self.unbind("<Button-1>")
            except Exception:
                pass
        self.after(100, lambda: self.bind("<Button-1>", close_if_outside))

    def _select_out_lang(self, value):
        self.out_lang_var.set(value)
        self.out_lang_toggle_btn.configure(text=f"  {value}  ▾")
        if self.out_lang_popup and self.out_lang_popup.winfo_exists():
            self.out_lang_popup.destroy()
            self.out_lang_popup = None
            
        def close_if_outside(e):
            try:
                px, py = popup.winfo_rootx(), popup.winfo_rooty()
                pw, ph = popup.winfo_width(), popup.winfo_height()
                if not (px <= e.x_root <= px+pw and py <= e.y_root <= py+ph):
                    popup.destroy()
                    self.lang_popup = None
                    self.unbind("<Button-1>")
                    self.unbind("<Configure>")
            except Exception:
                pass
        self.after(100, lambda: self.bind("<Button-1>", close_if_outside))
    
    def _select_lang(self, value):
        self.lang_var.set(value)
        self.lang_toggle_btn.configure(text=f"  {value}  ▾")
        if self.lang_popup and self.lang_popup.winfo_exists():
            self.lang_popup.destroy()
            self.lang_popup = None

    def _on_save_keys_toggle(self):
        if self.save_keys_var.get():
            openai_key = self.openai_entry.get().strip()
            if not openai_key:
                self.status_label.configure(text="Key를 먼저 입력해주세요", text_color="red")
                self.save_keys_var.set(False)
                return
            save_config({"openai": openai_key, "mode": self.mode_var.get(),
                         "language": self.lang_var.get()})
            self.status_label.configure(text="API Key 저장됨", text_color="green")
        else:
            cfg = load_config()
            cfg.pop("openai", None)
            save_config(cfg)
            self.status_label.configure(text="🗑 API Key 삭제됨", text_color="gray")

    def _toggle(self):
        if self.running:
            self._stop()
        else:
            self._start()

    def _start(self):
        self._meter_running = False
        if self._meter_thread and self._meter_thread.is_alive():
            self._meter_thread.join(timeout=2.0)

        openai_key = self.openai_entry.get().strip()
        if not openai_key:
            self.status_label.configure(text="API Key를 입력해주세요", text_color="red")
            return

        self.running = True
        self.ws_connected.clear()
        self.start_btn.configure(text="⏹  정지", fg_color="#e74c3c", hover_color="#c0392b")
        self.status_label.configure(text="🔗 연결 중...", text_color="gray")
        self.overlay = OverlayWindow(self)
        threading.Thread(target=self._run_translator, args=(openai_key,), daemon=True).start()

    def _stop(self):
        self.running = False
        self.ws_connected.clear()
        if self.ws_app:
            try:
                self.ws_app.close()
            except Exception:
                pass
        self.start_btn.configure(text="▶   시작", fg_color="#2563eb", hover_color="#1d4ed8")
        self.status_label.configure(text="정지됨", text_color="gray")
        self._start_volume_meter()

    def _set_status(self, text, color="gray"):
        self.after(0, lambda: self.status_label.configure(text=text, text_color=color))

    def _update_overlay(self, original, translated):
        if self.overlay:
            self.overlay.after(0, lambda: self.overlay.update_text(original, translated))

    def _run_translator(self, openai_key):
        openai_client = openai.OpenAI(api_key=openai_key)
        lang_code = LANGUAGES[self.lang_var.get()]
        mode = self.mode_var.get()
        translation_history = []

        def translate(text):
            context = "\n".join([
                f"원문: {h['original']}\n번역: {h['translated']}"
                for h in translation_history[-5:]
            ])
            out_lang_name = OUTPUT_LANGUAGES.get(self.out_lang_var.get(), "Korean")
            system_prompt = f"""You are an expert simultaneous interpreter specializing in real-time video call translation. Your goal is to produce translations that sound like they were originally spoken in {out_lang_name}.

TRANSLATION RULES:
1. Translate ONLY into {out_lang_name}. Never output any other language.
2. Use natural, conversational tone appropriate for {out_lang_name}.
3. Idioms and slang → find the equivalent expression in {out_lang_name}.
4. Technical terms → keep in English if commonly used.
5. Filler words (um, uh, like) → omit naturally.
6. If a sentence is incomplete → translate what's there without adding meaning.
7. Numbers, proper nouns, names → keep as-is.
8. Output ONLY the {out_lang_name} translation. Zero additional text."""
            user_prompt = f"{f'[이전 대화]{chr(10)}{context}{chr(10)}{chr(10)}' if context else ''}[번역할 문장]{chr(10)}{text}"

            # API 오류 시 재시도 (최대 3회)
            for attempt in range(3):
                try:
                    response = openai_client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=0.1,
                        max_tokens=500
                    )
                    translated = response.choices[0].message.content.strip()
                    translation_history.append({"original": text, "translated": translated})
                    return translated
                except Exception as e:
                    if attempt == 2:
                        return f"[번역 오류: {str(e)[:30]}]"
                    time.sleep(1)

        def on_open(ws):
            transcription_config = {
                "model": "whisper-1",
                "language": lang_code if lang_code else "en"
            }

            ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "type": "transcription",
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": TARGET_RATE},
                            "transcription": transcription_config,
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.5,
                                "prefix_padding_ms": 300,
                                "silence_duration_ms": 500
                            }
                        }
                    }
                }
            }))
            self.ws_connected.set()
            self._set_status("🎤 감지 중...", "green")

        def on_message(ws, message):
            data = json.loads(message)
            t = data.get("type", "")
            if t in ("input_audio_buffer.speech_started", "input_audio.speech_started"):
                self._set_status("🎤 감지됨...", "green")
            elif t in ("conversation.item.input_audio_transcription.completed",
                       "input_audio.transcription.completed"):
                transcript = data.get("transcript", "").strip()
                if transcript:
                    translated = translate(transcript)
                    self._set_status("✅ 번역 완료", "green")
                    self._update_overlay(transcript, translated)
            elif t == "error":
                error_msg = data.get("error", {}).get("message", "")
                if "buffer too small" in error_msg or "buffer is empty" in error_msg:
                    return
                self._set_status(f"❌ {error_msg}", "red")

        def on_error(ws, error):
            self._set_status("연결 오류 - 재연결 중...", "orange")

        def on_close(ws, *args):
            if self.running:
                self._set_status("연결 끊김 - 재연결 중...", "orange")
                time.sleep(3)
                if self.running:
                    threading.Thread(target=self._run_translator,
                                     args=(openai_key,), daemon=True).start()

        # 비용 절감: 무음 구간 감지
        last_audio_level = [0.0]

        def send_audio(ws, pcm16):
            if not self.ws_connected.is_set():
                return
            # 무음이면 전송 빈도 줄이기
            level = np.abs(pcm16.astype(np.float32)).mean()
            last_audio_level[0] = level
            try:
                ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm16.tobytes()).decode()
                }))
            except Exception:
                pass

        def resample_audio(audio, from_rate):
            if from_rate == TARGET_RATE:
                return audio
            g = gcd(from_rate, TARGET_RATE)
            return resample_poly(audio, TARGET_RATE // g, from_rate // g)

        headers = {"Authorization": f"Bearer {openai_key}"}
        self.ws_app = websocket.WebSocketApp(
            REALTIME_URL, header=headers,
            on_open=on_open, on_message=on_message,
            on_error=on_error, on_close=on_close,
        )

        def capture():
            self.ws_connected.wait()
            buf = np.array([], dtype=np.float32)

            if mode == "사용자 마이크":
                try:
                    selected = self.device_var.get()
                    if selected == "기본 장치" or not hasattr(self, '_device_list'):
                        mic_device = sd.default.device[0]
                        if mic_device < 0:
                            mic_device = next(
                                i for i, d in enumerate(sd.query_devices())
                                if d['max_input_channels'] > 0)
                    else:
                        match = next((d for d in self._device_list if d['name'] == selected), None)
                        mic_device = match['index'] if match else sd.default.device[0]
                    default_mic = sd.query_devices(mic_device, 'input')
                    CAPTURE_RATE = int(default_mic['default_samplerate'])
                except Exception:
                    mic_device = None
                    CAPTURE_RATE = 44100

                CHUNK = int(CAPTURE_RATE * 0.1)
                self._set_status(f"🎤 마이크 연결됨 ({CAPTURE_RATE}Hz)", "green")

                def mic_callback(indata, frames, t, status):
                    nonlocal buf
                    audio = indata[:, 0]
                    buf = np.concatenate([buf, audio])
                    target = int(CAPTURE_RATE * 0.1)
                    while len(buf) >= target:
                        chunk = buf[:target]
                        buf = buf[target:]
                        send_audio(self.ws_app,
                                   (resample_audio(chunk, CAPTURE_RATE) * 32768).astype(np.int16))

                while self.running:
                    try:
                        with sd.InputStream(device=mic_device, samplerate=CAPTURE_RATE,
                                            channels=1, dtype='float32',
                                            blocksize=CHUNK, callback=mic_callback):
                            while self.running:
                                time.sleep(0.1)
                    except Exception:
                        if not self.running:
                            break
                        self._set_status("⚠️ 마이크 재연결 중...", "orange")
                        time.sleep(2)
                        try:
                            mic_device = sd.default.device[0]
                            default_mic = sd.query_devices(mic_device, 'input')
                            CAPTURE_RATE = int(default_mic['default_samplerate'])
                            CHUNK = int(CAPTURE_RATE * 0.1)
                            self._set_status("🎤 마이크 재연결됨", "green")
                        except Exception:
                            pass
            else:
                pa = pyaudio.PyAudio()
                selected = self.device_var.get()
                try:
                    if selected == "기본 장치" or not hasattr(self, '_device_list') or not self._device_list:
                        speakers = pa.get_default_wasapi_loopback()
                        capture_rate = int(speakers["defaultSampleRate"])
                        channels = int(speakers["maxInputChannels"])
                        device_index = int(speakers["index"])
                    else:
                        match = next((d for d in self._device_list if d['name'] == selected), None)
                        if match:
                            capture_rate = match['sampleRate']
                            channels = match['channels']
                            device_index = match['index']
                        else:
                            speakers = pa.get_default_wasapi_loopback()
                            capture_rate = int(speakers["defaultSampleRate"])
                            channels = int(speakers["maxInputChannels"])
                            device_index = int(speakers["index"])
                    self._set_status(f"🔊 캡처 시작: {selected}", "green")
                except Exception as e:
                    self._set_status(f"❌ 장치 오류: {e}", "red")
                    pa.terminate()
                    return

                CHUNK = int(capture_rate * 0.1)

                def loop_callback(in_data, frame_count, time_info, status):
                    nonlocal buf
                    raw = np.frombuffer(in_data, dtype=np.float32)
                    if channels > 1:
                        raw = raw.reshape(-1, channels).mean(axis=1)
                    buf = np.concatenate([buf, raw])
                    target = int(capture_rate * 0.1)
                    while len(buf) >= target:
                        chunk = buf[:target]
                        buf = buf[target:]
                        send_audio(self.ws_app,
                                   (resample_audio(chunk, capture_rate) * 32768).astype(np.int16))
                    return (None, pyaudio.paContinue)

                while self.running:
                    try:
                        stream = pa.open(format=pyaudio.paFloat32, channels=channels,
                                         rate=capture_rate, input=True,
                                         input_device_index=device_index,
                                         frames_per_buffer=CHUNK, stream_callback=loop_callback)
                        stream.start_stream()
                        while self.running and stream.is_active():
                            time.sleep(0.1)
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        if not self.running:
                            break
                        self._set_status("⚠️ 오디오 재연결 중...", "orange")
                        time.sleep(2)
                        try:
                            speakers = pa.get_default_wasapi_loopback()
                            capture_rate = int(speakers["defaultSampleRate"])
                            channels = int(speakers["maxInputChannels"])
                            device_index = int(speakers["index"])
                            CHUNK = int(capture_rate * 0.1)
                            self._set_status("🔊 오디오 재연결됨", "green")
                        except Exception:
                            pass
                pa.terminate()

        threading.Thread(target=capture, daemon=True).start()

        # 비용 절감: 소리 있을 때만 force_commit
        def force_commit():
            while self.running:
                time.sleep(4)
                if not self.ws_connected.is_set():
                    continue
                if last_audio_level[0] < 0.001:  # 완전 무음이면 스킵
                    continue
                try:
                    self.ws_app.send(json.dumps({"type": "input_audio_buffer.commit"}))
                except Exception:
                    break

        threading.Thread(target=force_commit, daemon=True).start()
        self.ws_app.run_forever()

    def _hide_to_tray(self):
        self.withdraw()

    def _show_window(self):
        self.after(0, self.deiconify)

    def _start_tray(self):
        icon_img = create_tray_icon()
        menu = pystray.Menu(
            pystray.MenuItem("열기", lambda: self._show_window()),
            pystray.MenuItem("종료", lambda: self._quit()),
        )
        self.tray = pystray.Icon(APP_NAME, icon_img, APP_NAME, menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _quit(self):
        self._device_monitor_running = False
        self._stop()
        if self.tray:
            self.tray.stop()
        self.after(0, self.destroy)

if __name__ == "__main__":
    login = LoginWindow()
    login.mainloop()
    token = getattr(login, 'user_token', None)
    refresh = getattr(login, 'refresh_token', None)
    try:
        login.destroy()
    except Exception:
        pass

    if token:
        app = App(token, refresh)
        app.mainloop()