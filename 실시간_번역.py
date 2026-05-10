# ©12211326 장대현 (인하대학교) eosl1212@inha.edu
# 무단 수정 및 배포를 금합니다.

import json
import base64
import threading
import time
import tempfile
import os
import websocket
import numpy as np
import scipy.io.wavfile as wav
import pyaudiowpatch as pyaudio
import sounddevice as sd
import deepl
import pystray
import customtkinter as ctk
from PIL import Image, ImageDraw
from pathlib import Path
from scipy.signal import resample_poly
from math import gcd

APP_NAME = "실시간 번역기 v1.0.0  |  장대현"
APP_VERSION = "1.0.0"
APP_AUTHOR = "장대현 (인하대학교)"
APP_EMAIL = "eosl1212@inha.edu"

CONFIG_FILE = Path("config.json")
REALTIME_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"
TARGET_RATE = 24000

LANGUAGES = {
    "자동 감지": None,
    "영어": "en",
    "일본어": "ja",
    "중국어": "zh",
    "스페인어": "es",
    "프랑스어": "fr",
    "독일어": "de",
}

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f)

def create_tray_icon():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill=(59, 130, 246))
    draw.text((18, 18), "T", fill="white")
    return img

# ───────────── 오버레이 자막 창 ─────────────
class OverlayWindow(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__()
        self._app = app
        self.title("")
        self.geometry("700x500+300+500")
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.85)
        self.overrideredirect(True)
        self.configure(fg_color="#1a1a1a")

        self.MAX_ITEMS = 9999
        self._resize_data = None

        # 드래그 바
        self.drag_bar = ctk.CTkFrame(self, height=36, fg_color="#2a2a2a", corner_radius=0)
        self.drag_bar.pack(fill="x")
        self.drag_bar.pack_propagate(False)
        self.drag_bar.configure(cursor="fleur")
        self.drag_bar.bind("<ButtonPress-1>", self._start_drag)
        self.drag_bar.bind("<B1-Motion>", self._on_drag)

        close_btn = ctk.CTkButton(
            self.drag_bar, text="✕",
            width=32, height=26,
            fg_color="transparent", hover_color="#e74c3c",
            text_color="#888888", font=ctk.CTkFont(size=13),
            command=self._close
        )
        close_btn.pack(side="right", padx=6, pady=4)

        ctk.CTkLabel(
            self.drag_bar, text="크기",
            font=ctk.CTkFont(size=11), text_color="#888888"
        ).pack(side="right", padx=(0, 2), pady=4)

        self.font_slider = ctk.CTkSlider(
            self.drag_bar, from_=12, to=32,
            width=110, height=16, number_of_steps=20,
            command=self._on_font_change
        )
        self.font_slider.set(15)
        self.font_slider.pack(side="right", padx=(0, 8), pady=4)

        title_label = ctk.CTkLabel(
            self.drag_bar, text="☰  실시간 번역",
            font=ctk.CTkFont(size=12), text_color="#888888", cursor="fleur"
        )
        title_label.pack(side="left", padx=12, pady=4)
        title_label.bind("<ButtonPress-1>", self._start_drag)
        title_label.bind("<B1-Motion>", self._on_drag)

        self.scroll_frame = ctk.CTkScrollableFrame(
            self, fg_color="#1a1a1a", corner_radius=0
        )
        self.scroll_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        self.placeholder = ctk.CTkLabel(
            self.scroll_frame, text="번역 대기 중...",
            font=ctk.CTkFont(size=14), text_color="#555555"
        )
        self.placeholder.pack(pady=20)

        self.items = []
        self.all_labels = []  # (label, is_translated) 튜플 저장
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
                    wraplength=wrap
                )
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
            ("s",  dict(relx=0,   rely=1.0, relwidth=1,  height=S,   y=-S)),
            ("w",  dict(relx=0,   rely=0,   width=S,     relheight=1)),
            ("e",  dict(relx=1.0, rely=0,   width=S,     relheight=1, x=-S)),
            ("nw", dict(relx=0,   rely=0,   width=S*2,   height=S*2)),
            ("ne", dict(relx=1.0, rely=0,   width=S*2,   height=S*2,  x=-S*2)),
            ("sw", dict(relx=0,   rely=1.0, width=S*2,   height=S*2,  y=-S*2)),
            ("se", dict(relx=1.0, rely=1.0, width=S*2,   height=S*2,  x=-S*2, y=-S*2)),
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

        if len(self.items) >= self.MAX_ITEMS:
            oldest = self.items.pop()
            oldest.destroy()

        font_size = int(self.font_slider.get())
        card = ctk.CTkFrame(self.scroll_frame, fg_color="#2a2a2a", corner_radius=8)

        wrap = max(200, self.winfo_width() - 60)

        orig_label = ctk.CTkLabel(
            card, text=original,
            font=ctk.CTkFont(size=font_size),
            text_color="#aaaaaa", anchor="w",
            wraplength=wrap, justify="left"
        )
        orig_label.pack(anchor="w", padx=12, pady=(8, 2))

        trans_label = ctk.CTkLabel(
            card, text=translated,
            font=ctk.CTkFont(size=font_size + 6, weight="bold"),
            text_color="white", anchor="w",
            wraplength=wrap, justify="left"
        )
        trans_label.pack(anchor="w", padx=12, pady=(0, 8))

        self.all_labels.append((orig_label, False))
        self.all_labels.append((trans_label, True))

        self.items.insert(0, card)
        card.pack(fill="x", pady=3, padx=4)

        for item in self.items[1:]:
            item.pack_forget()
            item.pack(fill="x", pady=3, padx=4)

        self.scroll_frame._parent_canvas.yview_moveto(0.0)

# ───────────── 메인 GUI ─────────────
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(f"{APP_NAME}")
        self.resizable(False, False)
        self.update_idletasks()
        w, h = 480, 680
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.ws_app = None
        self.running = False
        self.ws_connected = threading.Event()
        self.overlay = None
        self.tray = None

        self.config = load_config()
        self._build_ui()
        self._start_tray()
        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)

    def _build_ui(self):
        self.configure(fg_color="#111111")

        # 헤더
        header = ctk.CTkFrame(self, fg_color="#1a1a1a", corner_radius=0, height=90)
        header.pack(fill="x")
        header.pack_propagate(False)

        left_frame = ctk.CTkFrame(header, fg_color="transparent")
        left_frame.pack(side="left", padx=24, fill="y")
        ctk.CTkLabel(
            left_frame, text="🌐  실시간 번역기",
            font=ctk.CTkFont(size=26, weight="bold"), text_color="white"
        ).pack(anchor="w", pady=(22, 2))

        right_frame = ctk.CTkFrame(header, fg_color="transparent")
        right_frame.pack(side="right", padx=24, fill="y")
        ctk.CTkLabel(
            right_frame, text=APP_AUTHOR,
            font=ctk.CTkFont(size=17, weight="bold"), text_color="#cccccc"
        ).pack(anchor="e", pady=(20, 2))
        ctk.CTkLabel(
            right_frame, text=APP_EMAIL,
            font=ctk.CTkFont(size=14), text_color="#555555"
        ).pack(anchor="e")

        # API 키
        self._section_label("API 키")
        key_frame = ctk.CTkFrame(self, fg_color="#1c1c1c", corner_radius=12)
        key_frame.pack(fill="x", padx=20, pady=(4, 0))

        ctk.CTkLabel(key_frame, text="OpenAI",
                     font=ctk.CTkFont(size=16), text_color="#888888", anchor="w"
                     ).pack(fill="x", padx=16, pady=(14, 2))
        openai_row = ctk.CTkFrame(key_frame, fg_color="transparent")
        openai_row.pack(fill="x", padx=16, pady=(0, 10))
        self.openai_entry = ctk.CTkEntry(
            openai_row, placeholder_text="sk-...", show="*",
            height=44, corner_radius=8,
            font=ctk.CTkFont(size=16),
            fg_color="#252525", border_color="#333333"
        )
        self.openai_entry.pack(side="left", fill="x", expand=True)
        if self.config.get("openai"):
            self.openai_entry.insert(0, self.config["openai"])
        self._eye_btn(openai_row, self.openai_entry)

        ctk.CTkLabel(key_frame, text="DeepL",
                     font=ctk.CTkFont(size=16), text_color="#888888", anchor="w"
                     ).pack(fill="x", padx=16, pady=(0, 2))
        deepl_row = ctk.CTkFrame(key_frame, fg_color="transparent")
        deepl_row.pack(fill="x", padx=16, pady=(0, 10))
        self.deepl_entry = ctk.CTkEntry(
            deepl_row, placeholder_text="xxxx:fx", show="*",
            height=44, corner_radius=8,
            font=ctk.CTkFont(size=16),
            fg_color="#252525", border_color="#333333"
        )
        self.deepl_entry.pack(side="left", fill="x", expand=True)
        if self.config.get("deepl"):
            self.deepl_entry.insert(0, self.config["deepl"])
        self._eye_btn(deepl_row, self.deepl_entry)

        self.save_keys_var = ctk.BooleanVar(value=bool(self.config.get("openai")))
        ctk.CTkCheckBox(
            key_frame, text="API 키 저장",
            variable=self.save_keys_var,
            command=self._on_save_keys_toggle,
            font=ctk.CTkFont(size=16),
            text_color="#888888",
            checkmark_color="white",
            fg_color="#2563eb",
            hover_color="#1d4ed8",
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

        # 언어 토글
        self.lang_visible = False
        self.lang_var = ctk.StringVar(value=self.config.get("language", "자동 감지"))
        self.lang_popup = None

        lang_toggle_frame = ctk.CTkFrame(self, fg_color="#1c1c1c", corner_radius=12)
        lang_toggle_frame.pack(fill="x", padx=20, pady=(16, 0))

        inner = ctk.CTkFrame(lang_toggle_frame, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)

        ctk.CTkLabel(
            inner, text="원문 언어",
            font=ctk.CTkFont(size=17, weight="bold"), text_color="#aaaaaa"
        ).pack(side="left")

        self.lang_toggle_btn = ctk.CTkButton(
            inner,
            text=f"  {self.lang_var.get()}  ▾",
            width=180, height=42, corner_radius=10,
            fg_color="#2563eb", hover_color="#1d4ed8",
            text_color="white",
            font=ctk.CTkFont(size=17, weight="bold"),
            command=self._toggle_lang
        )
        self.lang_toggle_btn.pack(side="right")

        # 하단 고정 영역
        self.bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.bottom_frame.pack(fill="x", side="bottom", pady=(0, 8))

        ctk.CTkLabel(
            self.bottom_frame, text=f"v{APP_VERSION}",
            font=ctk.CTkFont(size=12), text_color="#444444"
        ).pack(pady=(0, 8))

        self.start_btn = ctk.CTkButton(
            self.bottom_frame, text="▶   시작",
            font=ctk.CTkFont(size=22, weight="bold"),
            height=60, corner_radius=12,
            fg_color="#2563eb", hover_color="#1d4ed8",
            command=self._toggle
        )
        self.start_btn.pack(fill="x", padx=20, pady=(0, 8))

        self.status_label = ctk.CTkLabel(
            self.bottom_frame, text="대기 중",
            font=ctk.CTkFont(size=16), text_color="#555555"
        )
        self.status_label.pack(pady=(4, 0))

    def _eye_btn(self, parent, entry):
        def toggle():
            if entry.cget("show") == "*":
                entry.configure(show="")
                btn.configure(text="🙈")
            else:
                entry.configure(show="*")
                btn.configure(text="👁")
        btn = ctk.CTkButton(
            parent, text="👁", width=36, height=36,
            fg_color="#252525", hover_color="#333333",
            corner_radius=8, command=toggle
        )
        btn.pack(side="left", padx=(6, 0))

    def _section_label(self, text):
        ctk.CTkLabel(
            self, text=text, font=ctk.CTkFont(size=17, weight="bold"),
            text_color="#aaaaaa", anchor="w"
        ).pack(fill="x", padx=20, pady=(16, 4))

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
        self.lang_popup = popup

        for lang_name in LANGUAGES:
            ctk.CTkButton(
                popup, text=lang_name, height=42,
                fg_color="transparent", hover_color="#2a2a2a",
                text_color="white", anchor="w", corner_radius=8,
                font=ctk.CTkFont(size=16),
                command=lambda l=lang_name: self._select_lang(l)
            ).pack(fill="x", padx=4, pady=2)

    def _select_lang(self, value):
        self.lang_var.set(value)
        self.lang_toggle_btn.configure(text=f"  {value}  ▾")
        if self.lang_popup and self.lang_popup.winfo_exists():
            self.lang_popup.destroy()
            self.lang_popup = None

    def _on_save_keys_toggle(self):
        if self.save_keys_var.get():
            openai_key = self.openai_entry.get().strip()
            deepl_key = self.deepl_entry.get().strip()
            if not openai_key or not deepl_key:
                self.status_label.configure(text="Key를 먼저 입력해주세요", text_color="red")
                self.save_keys_var.set(False)
                return
            save_config({
                "openai": openai_key,
                "deepl": deepl_key,
                "mode": self.mode_var.get(),
                "language": self.lang_var.get(),
            })
            self.status_label.configure(text="API Key 저장됨", text_color="green")
        else:
            # 체크 해제 시 Key 삭제
            cfg = load_config()
            cfg.pop("openai", None)
            cfg.pop("deepl", None)
            save_config(cfg)
            self.status_label.configure(text="🗑 API Key 삭제됨", text_color="gray")

    def _toggle(self):
        if self.running:
            self._stop()
        else:
            self._start()

    def _start(self):
        openai_key = self.openai_entry.get().strip()
        deepl_key = self.deepl_entry.get().strip()
        if not openai_key or not deepl_key:
            self.status_label.configure(text="API Key를 입력해주세요", text_color="red")
            return

        self.running = True
        self.ws_connected.clear()
        self.start_btn.configure(text="⏹  정지", fg_color="#e74c3c", hover_color="#c0392b")
        self.status_label.configure(text="🔗 연결 중...", text_color="gray")
        self.overlay = OverlayWindow(self)
        threading.Thread(target=self._run_translator, args=(openai_key, deepl_key), daemon=True).start()

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

    def _set_status(self, text, color="gray"):
        self.after(0, lambda: self.status_label.configure(text=text, text_color=color))

    def _update_overlay(self, original, translated):
        if self.overlay:
            self.overlay.after(0, lambda: self.overlay.update_text(original, translated))

    def _run_translator(self, openai_key, deepl_key):
        deepl_client = deepl.Translator(deepl_key)
        lang_code = LANGUAGES[self.lang_var.get()]
        mode = self.mode_var.get()

        def translate(text):
            return deepl_client.translate_text(text, target_lang="KO").text

        def on_open(ws):
            ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "input_audio_format": "pcm16",
                    "input_audio_transcription": {
                        "model": "whisper-1",
                        **({"language": lang_code} if lang_code else {})
                    },
                    "turn_detection": {"type": "semantic_vad", "eagerness": "high"},
                    "modalities": ["text"],
                    "instructions": "Never respond. You are a transcription tool only."
                }
            }))
            self.ws_connected.set()
            self._set_status("🎤 감지 중...", "green")

        def on_message(ws, message):
            data = json.loads(message)
            t = data.get("type", "")
            if t == "input_audio_buffer.speech_started":
                self._set_status("🎤 감지됨...", "green")
            elif t == "conversation.item.input_audio_transcription.completed":
                transcript = data.get("transcript", "").strip()
                if transcript:
                    translated = translate(transcript)
                    self._set_status("번역 완료", "green")
                    self._update_overlay(transcript, translated)
            elif t == "error":
                error_msg = data.get("error", {}).get("message", "")
                if "buffer too small" in error_msg or "buffer is empty" in error_msg:
                    return
                self._set_status(f"❌ {error_msg}", "red")

        def on_error(ws, error):
            self._set_status("연결 오류", "red")

        def on_close(ws, *args):
            if self.running:
                self._set_status("연결 끊김", "red")

        def send_audio(ws, pcm16):
            if not self.ws_connected.is_set():
                return
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

        headers = {
            "Authorization": f"Bearer {openai_key}",
            "OpenAI-Beta": "realtime=v1"
        }
        self.ws_app = websocket.WebSocketApp(
            REALTIME_URL, header=headers,
            on_open=on_open, on_message=on_message,
            on_error=on_error, on_close=on_close,
        )

        def capture():
            self.ws_connected.wait()
            buf = np.array([], dtype=np.float32)

            if mode == "사용자 마이크":
                # 시스템 기본 마이크 자동 감지
                try:
                    mic_device = sd.default.device[0]
                    if mic_device < 0:
                        mic_device = next(
                            i for i, d in enumerate(sd.query_devices())
                            if d['max_input_channels'] > 0
                        )
                    default_mic = sd.query_devices(mic_device, 'input')
                    mic_name = default_mic['name']
                    CAPTURE_RATE = int(default_mic['default_samplerate'])
                except Exception as e:
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
                        send_audio(self.ws_app, (resample_audio(chunk, CAPTURE_RATE) * 32768).astype(np.int16))

                try:
                    with sd.InputStream(
                    device=mic_device,
                        samplerate=CAPTURE_RATE,
                        channels=1,
                        dtype='float32',
                        blocksize=CHUNK,
                        callback=mic_callback
                    ):
                        while self.running:
                            time.sleep(0.1)
                except Exception as e:
                    self._set_status(f"❌ 마이크 오류: {e}", "red")
            else:
                pa = pyaudio.PyAudio()
                speakers = pa.get_default_wasapi_loopback()
                capture_rate = int(speakers["defaultSampleRate"])
                channels = speakers["maxInputChannels"]
                device_index = speakers["index"]
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
                        send_audio(self.ws_app, (resample_audio(chunk, capture_rate) * 32768).astype(np.int16))
                    return (None, pyaudio.paContinue)

                stream = pa.open(format=pyaudio.paFloat32, channels=channels,
                                 rate=capture_rate, input=True,
                                 input_device_index=device_index,
                                 frames_per_buffer=CHUNK, stream_callback=loop_callback)
                stream.start_stream()
                while self.running and stream.is_active():
                    time.sleep(0.1)
                stream.stop_stream()
                stream.close()
                pa.terminate()

        threading.Thread(target=capture, daemon=True).start()

        def force_commit():
            while self.running:
                time.sleep(4)
                if not self.ws_connected.is_set():
                    continue
                try:
                    self.ws_app.send(json.dumps({"type": "input_audio_buffer.commit"}))
                    self.ws_app.send(json.dumps({
                        "type": "response.create",
                        "response": {"modalities": ["text"], "max_output_tokens": 1}
                    }))
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
        self._stop()
        if self.tray:
            self.tray.stop()
        self.after(0, self.destroy)

if __name__ == "__main__":
    app = App()
    app.mainloop()