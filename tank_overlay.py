# -*- coding: utf-8 -*-
"""
Оверлей статистики боёв (распознавание урона с экрана).

Экраны (машина состояний):
    MAIN      — хаб: танк, статистика сессии, кнопки.
    BATTLE    — активный бой: крупный урон.
    SETTINGS  — настройки: зона, хоткеи, танки, прозрачность.

Переходы:
    MAIN      -> BATTLE    хоткей начала боя (F1)
    BATTLE    -> MAIN      хоткей победы (F2) или поражения (F3)
    MAIN      <-> SETTINGS кнопками в интерфейсе

Цифры уже вшиты в recognition.py — этап разметки убран.

Зависимости: numpy, mss, keyboard, Pillow
"""

import os
import sys
import queue
import logging
import threading
import tkinter as tk
from datetime import datetime
from typing import Optional

import numpy as np

from storage import Settings, History, GLYPHS_FILE
from recognition import GlyphStore, RoiWatcher, REQUIRED_DIGITS, GLYPH_W, GLYPH_H

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("overlay")

# ──────────────────────────── Оформление ────────────────────────────

BG = "#12151a"
PANEL = "#1b2029"
FG = "#e8eaed"
DIM = "#8b93a1"
ACCENT = "#ffb020"        # урон
GREEN = "#4ade80"         # победы
RED = "#f87171"
BLUE = "#60a5fa"

FONT = "Segoe UI"

GLYPH_SCALE = 6

SCREEN_SIZES = {
    "MAIN": (370, 235),
    "BATTLE": (310, 155),
    "SETTINGS": (430, 520),
}


def font(size: int, bold: bool = False) -> tuple:
    return (FONT, size, "bold") if bold else (FONT, size)


# ──────────────────────────── Редактор зоны (ROI) ────────────────────────────

class RoiEditor(tk.Toplevel):
    """
    Полупрозрачная рамка выбора области + лупа ×4.
    - Полностью прозрачная заливка, только чёткий контур.
    - Ручка изменения размера 4×4.
    - При ресайзе плашка не смещается.
    - Вторая перемещаемая лупа (×4), показывает увеличенные пиксели ROI.
    """

    MIN_SIZE = 16
    GRIP = 4
    DEFAULT_H = 28
    MAG_SCALE = 4

    def __init__(self, master, roi: dict, on_change):
        super().__init__(master)
        self.on_change = on_change
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.85)
        self.configure(bg="#00ff00")
        try:
            self.attributes("-transparentcolor", "#00ff00")
        except tk.TclError:
            self.attributes("-alpha", 0.35)

        x = int(roi.get("x", 200))
        y = int(roi.get("y", 200))
        w = max(self.MIN_SIZE, int(roi.get("width", 140)))
        h = max(self.MIN_SIZE, int(roi.get("height", self.DEFAULT_H)))
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.canvas = tk.Canvas(
            self, bg="#00ff00", highlightthickness=0,
            bd=0, cursor="fleur"
        )
        self.canvas.pack(fill="both", expand=True)
        self._border_id = self.canvas.create_rectangle(
            1, 1, w - 2, h - 2,
            outline="#ff2d55", width=2, fill=""
        )

        self.grip = tk.Frame(self, bg="#ffffff", cursor="bottom_right_corner",
                             width=self.GRIP, height=self.GRIP)
        self.grip.place(relx=1.0, rely=1.0, anchor="se")

        self._drag = None
        self._resize = None
        self._mag = None

        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._do_drag)
        self.canvas.bind("<Double-Button-1>", lambda e: self.close())
        self.grip.bind("<ButtonPress-1>", self._start_resize)
        self.grip.bind("<B1-Motion>", self._do_resize)
        self.grip.bind("<ButtonRelease-1>", self._end_resize)

        self._create_magnifier()
        self.after(50, self._update_magnifier)

        self.bind("<Configure>", self._on_configure)

    def _start_drag(self, e):
        if self._resize:
            return
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _do_drag(self, e):
        if not self._drag:
            return
        x = e.x_root - self._drag[0]
        y = e.y_root - self._drag[1]
        self.geometry(f"+{x}+{y}")
        self._notify()
        self._update_magnifier()

    def _start_resize(self, e):
        self._drag = None
        self._resize = (
            e.x_root,
            e.y_root,
            self.winfo_x(),
            self.winfo_y(),
            self.winfo_width(),
            self.winfo_height(),
        )

    def _do_resize(self, e):
        if not self._resize:
            return
        x0, y0, left, top, w0, h0 = self._resize
        dw = e.x_root - x0
        dh = e.y_root - y0
        w = max(self.MIN_SIZE, w0 + dw)
        h = max(self.MIN_SIZE, h0 + dh)
        self.geometry(f"{w}x{h}+{left}+{top}")
        self._notify()
        self._update_magnifier()

    def _end_resize(self, e):
        self._resize = None

    def _on_configure(self, e):
        if e.widget is not self:
            return
        w, h = e.width, e.height
        self.canvas.coords(self._border_id, 1, 1, w - 2, h - 2)

    def _create_magnifier(self):
        self._mag = tk.Toplevel(self)
        self._mag.overrideredirect(True)
        self._mag.attributes("-topmost", True)
        self._mag.attributes("-alpha", 0.98)
        self._mag.configure(bg="#111111")

        rw = max(self.MIN_SIZE, self.winfo_width())
        rh = max(self.MIN_SIZE, self.winfo_height())
        mw = rw * self.MAG_SCALE
        mh = rh * self.MAG_SCALE

        mx = self.winfo_x() + rw + 16
        my = self.winfo_y()
        self._mag.geometry(f"{mw}x{mh}+{mx}+{my}")

        self._mag_canvas = tk.Canvas(
            self._mag,
            bg="#111111",
            highlightthickness=1,
            highlightbackground="#ff2d55",
            bd=0,
            cursor="fleur",
            width=mw,
            height=mh,
        )
        self._mag_canvas.pack(fill="both", expand=True)

        self._mag_drag = None
        self._mag_canvas.bind("<ButtonPress-1>", self._mag_start_drag)
        self._mag_canvas.bind("<B1-Motion>", self._mag_do_drag)

        self._mag_photo = None

    def _mag_start_drag(self, e):
        self._mag_drag = (
            e.x_root - self._mag.winfo_x(),
            e.y_root - self._mag.winfo_y(),
        )

    def _mag_do_drag(self, e):
        if not self._mag_drag:
            return
        x = e.x_root - self._mag_drag[0]
        y = e.y_root - self._mag_drag[1]
        self._mag.geometry(f"+{x}+{y}")

    def _update_magnifier(self):
        if not self.winfo_exists() or not self._mag or not self._mag.winfo_exists():
            return

        try:
            import mss
            import numpy as np
            from PIL import Image, ImageTk
        except ImportError:
            if not getattr(self, "_pil_warned", False):
                print("[magnifier] Нужен пакет Pillow. Установи: python -m pip install Pillow")
                self._pil_warned = True
            if self.winfo_exists():
                self.after(1000, self._update_magnifier)
            return

        try:
            x = self.winfo_x()
            y = self.winfo_y()
            w = max(self.MIN_SIZE, self.winfo_width())
            h = max(self.MIN_SIZE, self.winfo_height())

            with mss.MSS() as sct:
                monitor = {"left": x, "top": y, "width": w, "height": h}
                shot = np.array(sct.grab(monitor))

            img = Image.fromarray(shot[:, :, [2, 1, 0]])
            img = img.resize(
                (w * self.MAG_SCALE, h * self.MAG_SCALE),
                Image.NEAREST
            )

            self._mag_photo = ImageTk.PhotoImage(img)

            mw = w * self.MAG_SCALE
            mh = h * self.MAG_SCALE
            self._mag.geometry(f"{mw}x{mh}")
            self._mag_canvas.config(width=mw, height=mh)
            self._mag_canvas.delete("all")
            self._mag_canvas.create_image(0, 0, anchor="nw", image=self._mag_photo)

        except Exception as e:
            print(f"[magnifier] error: {e}")

        if self.winfo_exists():
            self.after(70, self._update_magnifier)

    def _notify(self):
        self.update_idletasks()
        self.on_change({
            "x": self.winfo_x(),
            "y": self.winfo_y(),
            "width": self.winfo_width(),
            "height": self.winfo_height(),
        })

    def close(self):
        self._notify()
        if self._mag and self._mag.winfo_exists():
            self._mag.destroy()
        self.destroy()


# ──────────────────────────── Приложение ────────────────────────────

class OverlayApp:
    def __init__(self):
        self.settings = Settings()
        self.history = History()
        self.glyphs = GlyphStore()

        self.state = "MAIN"
        self.current_damage = 0
        self.session_id = self._new_session_id()
        self.roi_editor: Optional[RoiEditor] = None
        self.binding_action: Optional[str] = None

        self.ui_queue: "queue.Queue" = queue.Queue()
        self.stop_event = threading.Event()

        self._build_window()
        self._build_screens()

        self.watcher = RoiWatcher(self._watcher_config, self.glyphs, self.ui_queue, self.stop_event)
        self.watcher.start()

        self._check_resolution()
        self._register_hotkeys()

        # Цифры уже готовы — сразу на главный экран
        self.set_state("MAIN")
        self.root.after(100, self._poll_queue)

    def _watcher_config(self) -> dict:
        s = self.settings
        return {
            "roi": s.get("roi"),
            "stable_frames": s.get("stable_frames", 2),
            "count_on_change": s.get("count_on_change", False),
            # пустые кадры отсекаются до OCR — экономия процессора
            "skip_empty_frames": s.get("skip_empty_frames", True),
            "white_threshold": s.get("white_threshold", 185),
            "max_spread": s.get("max_spread", 40),
            # если подпись не прочиталась, плашка пропускается:
            # иначе «Урон с вашей помощью» может попасть в статистику
            "count_unknown_caption": s.get("count_unknown_caption", False),
            "digit_retry": s.get("digit_retry", True),
        }

    @staticmethod
    def _new_session_id() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-%f")

    def _screen_resolution(self):
        return (self.root.winfo_screenwidth(), self.root.winfo_screenheight())

    def _check_resolution(self):
        if self.glyphs.is_complete() and not self.glyphs.resolution_matches(self._screen_resolution()):
            log.warning("Разрешение экрана изменилось — шаблоны могут работать хуже")

    # ──────────────── окно ────────────────

    def _build_window(self):
        self.root = tk.Tk()
        self.root.title("Статистика боёв")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", float(self.settings.get("alpha", 0.85)))
        self.root.configure(bg=BG)

        w, h = SCREEN_SIZES["MAIN"]
        x = self.settings.get("window_x")
        y = self.settings.get("window_y")
        if x is None or y is None:
            x = self.root.winfo_screenwidth() - w - 60
            y = self.root.winfo_screenheight() - h - 80
        self.root.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

        self.container = tk.Frame(self.root, bg=BG)
        self.container.pack(fill="both", expand=True)

        self.root.bind("<Escape>", lambda e: self.shutdown())
        self.root.bind("<Control-q>", lambda e: self.shutdown())

        self._drag = None
        self.root.bind("<ButtonPress-1>", self._start_move, add="+")
        self.root.bind("<B1-Motion>", self._do_move, add="+")
        self.root.bind("<ButtonRelease-1>", self._stop_move, add="+")

    def _start_move(self, e):
        if isinstance(e.widget, (tk.Button, tk.Entry, tk.Listbox, tk.Scale, tk.Checkbutton)):
            self._drag = None
            return
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _do_move(self, e):
        if not self._drag:
            return
        self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _stop_move(self, e):
        if self._drag:
            self.settings.set("window_x", self.root.winfo_x())
            self.settings.set("window_y", self.root.winfo_y())
            self.settings.save()
        self._drag = None

    def _resize_for_state(self, state: str):
        w, h = SCREEN_SIZES[state]
        self.root.geometry(f"{w}x{h}")

    # ──────────────── экраны ────────────────

    def _build_screens(self):
        self.screens = {}
        for name, builder in (
            ("MAIN", self._build_main),
            ("BATTLE", self._build_battle),
            ("SETTINGS", self._build_settings),
        ):
            frame = tk.Frame(self.container, bg=BG)
            self.screens[name] = frame
            builder(frame)

    def set_state(self, state: str):
        if state == "CALIBRATE":
            state = "MAIN"

        self.state = state
        for name, frame in self.screens.items():
            frame.pack_forget()
        self.screens[state].pack(fill="both", expand=True, padx=12, pady=10)
        self._resize_for_state(state)

        if state == "BATTLE":
            self.watcher.mode = "battle"
            self._refresh_battle()
        else:
            self.watcher.mode = "idle"
            if state == "MAIN":
                self._refresh_main()
            elif state == "SETTINGS":
                self._refresh_settings()

    # ---------- главный экран ----------

    def _build_main(self, root_frame: tk.Frame):
        top = tk.Frame(root_frame, bg=BG)
        top.pack(fill="x")
        self.main_tank = tk.Label(top, text="—", font=font(13, True), fg=FG, bg=BG, anchor="w")
        self.main_tank.pack(side="left")
        tk.Button(top, text="✕", font=font(9), bg=BG, fg=DIM, relief="flat", bd=0,
                  command=self.shutdown).pack(side="right")

        self.main_status = tk.Label(root_frame, text="", font=font(9), fg=GREEN, bg=BG, anchor="w")
        self.main_status.pack(fill="x", pady=(0, 8))

        stats = tk.Frame(root_frame, bg=PANEL)
        stats.pack(fill="x", ipady=6)
        self.main_stats = {}
        for i, (key, title) in enumerate((("battles", "Бои"), ("avg_damage", "Ср. урон"),
                                          ("win_rate", "Побед %"))):
            cell = tk.Frame(stats, bg=PANEL)
            cell.grid(row=0, column=i, padx=10)
            value = tk.Label(cell, text="0", font=font(15, True), fg=GREEN, bg=PANEL)
            value.pack()
            tk.Label(cell, text=title, font=font(8), fg=DIM, bg=PANEL).pack()
            self.main_stats[key] = value
        stats.grid_columnconfigure((0, 1, 2), weight=1)

        self.main_total = tk.Label(root_frame, text="", font=font(9), fg=DIM, bg=BG, anchor="w")
        self.main_total.pack(fill="x", pady=(6, 0))
        self.main_lifetime = tk.Label(root_frame, text="", font=font(9), fg=DIM, bg=BG, anchor="w")
        self.main_lifetime.pack(fill="x")

        controls = tk.Frame(root_frame, bg=BG)
        controls.pack(fill="x", side="bottom", pady=(8, 0))

        self.tank_var = tk.StringVar(value=self.settings.get("selected_tank") or "Танк не выбран")
        self.tank_menu = tk.OptionMenu(controls, self.tank_var, "")
        self.tank_menu.config(font=font(9), bg=PANEL, fg=FG, relief="flat", bd=0,
                              highlightthickness=0, activebackground=BLUE, width=12)
        self.tank_menu["menu"].config(bg=PANEL, fg=FG, font=font(9))
        self.tank_menu.pack(side="left")

        tk.Button(controls, text="Настройки", font=font(9), bg=PANEL, fg=FG, relief="flat",
                  bd=0, command=lambda: self.set_state("SETTINGS")).pack(side="right")
        tk.Button(controls, text="Сброс сессии", font=font(9), bg=PANEL, fg=DIM, relief="flat",
                  bd=0, command=self._reset_session).pack(side="right", padx=6)

    def _rebuild_tank_menu(self):
        menu = self.tank_menu["menu"]
        menu.delete(0, "end")
        tanks = self.settings.get("tanks", [])
        if not tanks:
            menu.add_command(label="Список пуст", command=lambda: None)
            self.tank_var.set("Танк не выбран")
            return
        for name in tanks:
            menu.add_command(label=name, command=lambda n=name: self._select_tank(n))
        self.tank_var.set(self.settings.get("selected_tank") or "Танк не выбран")

    def _select_tank(self, name: str):
        previous = self.settings.get("selected_tank")
        self.settings.select_tank(name)
        self.tank_var.set(name)
        if name != previous:
            self.session_id = self._new_session_id()
        self._refresh_main()

    def _reset_session(self):
        self.session_id = self._new_session_id()
        self._refresh_main()

    def _refresh_main(self):
        self._rebuild_tank_menu()
        tank = self.settings.get("selected_tank")
        self.main_tank.config(text=tank or "Танк не выбран")

        hk = self.settings.get("hotkeys", {})
        if not tank:
            self.main_status.config(text="Выберите танк, чтобы начать", fg=ACCENT)
        else:
            self.main_status.config(
                text=f"Готов к бою · {hk.get('battle_start', 'f1').upper()} — начать", fg=GREEN)

        st = self.history.session_stats(self.session_id)
        for key, label in self.main_stats.items():
            label.config(text=str(st.get(key, 0)))
        self.main_total.config(text=f"Урон за сессию: {st['total_damage']}")

        if tank:
            lt = self.history.tank_stats(tank)
            self.main_lifetime.config(
                text=f"Всего на танке: {lt['battles']} боёв · ср. {lt['avg_damage']} · {lt['win_rate']}%"
            )
        else:
            self.main_lifetime.config(text="")

    # ---------- боевой экран ----------

    def _build_battle(self, root_frame: tk.Frame):
        self.battle_tank = tk.Label(root_frame, text="", font=font(9), fg=DIM, bg=BG, anchor="w")
        self.battle_tank.pack(fill="x")

        self.battle_damage = tk.Label(root_frame, text="0", font=(FONT, 42, "bold"),
                                      fg=ACCENT, bg=BG)
        self.battle_damage.pack(pady=(2, 0))

        self.battle_sub = tk.Label(root_frame, text="", font=font(9), fg=DIM, bg=BG)
        self.battle_sub.pack()

        hk = self.settings.get("hotkeys", {})
        self.battle_keys = tk.Label(
            root_frame,
            text=f"{hk.get('victory', 'f2').upper()} — победа   ·   {hk.get('defeat', 'f3').upper()} — поражение",
            font=font(8), fg="#5b6472", bg=BG,
        )
        self.battle_keys.pack(side="bottom")

    def _refresh_battle(self):
        self.battle_tank.config(text=self.settings.get("selected_tank") or "—")
        self.battle_damage.config(text=str(self.current_damage))
        st = self.history.session_stats(self.session_id)
        self.battle_sub.config(text=f"сессия: ср. {st['avg_damage']} · {st['win_rate']}%")
        hk = self.settings.get("hotkeys", {})
        self.battle_keys.config(
            text=f"{hk.get('victory', 'f2').upper()} — победа   ·   {hk.get('defeat', 'f3').upper()} — поражение"
        )

    # ---------- экран настроек ----------

    def _build_settings(self, root_frame: tk.Frame):
        tk.Label(root_frame, text="НАСТРОЙКИ", font=font(11, True), fg=BLUE, bg=BG).pack(anchor="w")

        # Зона распознавания
        self._section(root_frame, "Зона распознавания")
        roi_box = tk.Frame(root_frame, bg=BG)
        roi_box.pack(fill="x")
        self.roi_vars = {}
        for i, (key, title) in enumerate((("x", "X"), ("y", "Y"), ("width", "Ш"), ("height", "В"))):
            tk.Label(roi_box, text=title, font=font(9), fg=DIM, bg=BG).grid(row=0, column=i * 2, padx=(0, 2))
            var = tk.StringVar(value=str((self.settings.get("roi") or {}).get(key, 0)))
            entry = tk.Entry(roi_box, textvariable=var, width=5, font=font(9), bg=PANEL, fg=FG,
                             relief="flat", insertbackground=FG, justify="center")
            entry.grid(row=0, column=i * 2 + 1, padx=(0, 8))
            self.roi_vars[key] = var
        tk.Button(root_frame, text="Показать рамку на экране", font=font(9), bg=PANEL, fg=FG,
                  relief="flat", bd=0, command=self._open_roi_editor).pack(fill="x", pady=(6, 0))
        tk.Label(root_frame, text="Рамка не должна перекрывать это окно.",
                 font=font(8), fg="#5b6472", bg=BG, anchor="w").pack(fill="x")

        # Горячие клавиши
        self._section(root_frame, "Горячие клавиши")
        self.hotkey_buttons = {}
        for action, title in (("battle_start", "Начало боя"), ("victory", "Победа"), ("defeat", "Поражение")):
            row = tk.Frame(root_frame, bg=BG)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=title, font=font(9), fg=DIM, bg=BG, width=12, anchor="w").pack(side="left")
            btn = tk.Button(row, text=self.settings.get("hotkeys", {}).get(action, "—").upper(),
                            font=font(9, True), bg=PANEL, fg=FG, relief="flat", bd=0, width=10,
                            command=lambda a=action: self._bind_hotkey(a))
            btn.pack(side="left")
            self.hotkey_buttons[action] = btn

        # Танки
        self._section(root_frame, "Танки")
        add_row = tk.Frame(root_frame, bg=BG)
        add_row.pack(fill="x")
        self.tank_entry = tk.Entry(add_row, font=font(9), bg=PANEL, fg=FG, relief="flat",
                                   insertbackground=FG)
        self.tank_entry.pack(side="left", fill="x", expand=True, ipady=3)
        tk.Button(add_row, text="Добавить", font=font(9), bg=PANEL, fg=GREEN, relief="flat",
                  bd=0, command=self._add_tank).pack(side="left", padx=(6, 0))

        self.tank_list = tk.Listbox(root_frame, height=4, font=font(9), bg=PANEL, fg=FG,
                                    relief="flat", highlightthickness=0, selectbackground=BLUE,
                                    activestyle="none")
        self.tank_list.pack(fill="x", pady=(4, 2))
        tk.Button(root_frame, text="Удалить выбранный", font=font(9), bg=PANEL, fg=RED,
                  relief="flat", bd=0, command=self._remove_tank).pack(fill="x")

        # Внешний вид
        self._section(root_frame, "Внешний вид")
        self.alpha_var = tk.DoubleVar(value=float(self.settings.get("alpha", 0.85)))
        tk.Scale(root_frame, from_=0.3, to=1.0, resolution=0.05, orient="horizontal",
                 variable=self.alpha_var, command=self._on_alpha, bg=BG, fg=DIM,
                 troughcolor=PANEL, highlightthickness=0, relief="flat",
                 font=font(8), showvalue=True, sliderrelief="flat").pack(fill="x")

        self.invert_var = tk.BooleanVar(value=bool(self.settings.get("invert", False)))
        tk.Checkbutton(root_frame, text="Тёмные цифры на светлом фоне", variable=self.invert_var,
                       font=font(9), fg=DIM, bg=BG, selectcolor=PANEL, relief="flat",
                       activebackground=BG, activeforeground=FG,
                       command=self._on_invert).pack(anchor="w", pady=(4, 0))

        bottom = tk.Frame(root_frame, bg=BG)
        bottom.pack(fill="x", side="bottom", pady=(10, 0))
        tk.Button(bottom, text="Сохранить и вернуться", font=font(9, True), bg=PANEL, fg=GREEN,
                  relief="flat", bd=0, command=self._save_settings).pack(side="right")

    def _section(self, parent, title: str):
        tk.Label(parent, text=title.upper(), font=font(8, True), fg="#5b6472",
                 bg=BG, anchor="w").pack(fill="x", pady=(10, 3))

    def _refresh_settings(self):
        roi = self.settings.get("roi") or {}
        for key, var in self.roi_vars.items():
            var.set(str(roi.get(key, 0)))
        self.tank_list.delete(0, "end")
        for name in self.settings.get("tanks", []):
            self.tank_list.insert("end", name)
        for action, btn in self.hotkey_buttons.items():
            btn.config(text=self.settings.get("hotkeys", {}).get(action, "—").upper())

    def _open_roi_editor(self):
        if self.roi_editor is not None:
            try:
                self.roi_editor.close()
            except Exception:
                pass
        self.roi_editor = RoiEditor(self.root, self.settings.get("roi") or {}, self._on_roi_change)

    def _on_roi_change(self, roi: dict):
        self.settings.set("roi", roi)
        for key, var in self.roi_vars.items():
            var.set(str(roi.get(key, 0)))

    def _on_alpha(self, _value=None):
        alpha = float(self.alpha_var.get())
        self.root.attributes("-alpha", alpha)
        self.settings.set("alpha", alpha)

    def _on_invert(self):
        self.settings.set("invert", bool(self.invert_var.get()))

    def _add_tank(self):
        name = self.tank_entry.get().strip()
        if self.settings.add_tank(name):
            self.tank_entry.delete(0, "end")
            self._refresh_settings()

    def _remove_tank(self):
        sel = self.tank_list.curselection()
        if not sel:
            return
        self.settings.remove_tank(self.tank_list.get(sel[0]))
        self._refresh_settings()

    def _save_settings(self):
        roi = {}
        for key, var in self.roi_vars.items():
            try:
                roi[key] = max(0, int(float(var.get())))
            except (ValueError, TypeError):
                roi[key] = (self.settings.get("roi") or {}).get(key, 0)
        roi["width"] = max(8, roi.get("width", 0))
        roi["height"] = max(8, roi.get("height", 0))
        self.settings.set("roi", roi)
        self.settings.set("alpha", float(self.alpha_var.get()))
        self.settings.set("invert", bool(self.invert_var.get()))
        self.settings.save()

        if self.roi_editor is not None:
            try:
                self.roi_editor.close()
            except Exception:
                pass
            self.roi_editor = None

        self._register_hotkeys()
        self.set_state("MAIN")

    # ──────────────── горячие клавиши ────────────────

    def _register_hotkeys(self):
        try:
            import keyboard
        except ImportError:
            log.error("Модуль keyboard не установлен — горячие клавиши недоступны")
            return
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        hk = self.settings.get("hotkeys", {})
        mapping = (
            (hk.get("battle_start", "f1"), "battle_start"),
            (hk.get("victory", "f2"), "victory"),
            (hk.get("defeat", "f3"), "defeat"),
        )
        for key, action in mapping:
            if not key:
                continue
            try:
                keyboard.add_hotkey(key, lambda a=action: self.ui_queue.put(("hotkey", a)))
            except Exception as e:
                log.warning("Не удалось назначить клавишу %s: %s", key, e)

    def _bind_hotkey(self, action: str):
        try:
            import keyboard
        except ImportError:
            return
        self.binding_action = action
        self.hotkey_buttons[action].config(text="нажмите…", fg=ACCENT)

        def capture():
            try:
                event = keyboard.read_event(suppress=False)
                while event.event_type != "down":
                    event = keyboard.read_event(suppress=False)
                self.ui_queue.put(("bind", (action, event.name)))
            except Exception as e:
                log.warning("Ошибка перехвата клавиши: %s", e)
                self.ui_queue.put(("bind", (action, None)))

        threading.Thread(target=capture, daemon=True).start()

    def _apply_binding(self, action: str, key: Optional[str]):
        self.binding_action = None
        if key:
            hk = dict(self.settings.get("hotkeys", {}))
            hk[action] = key
            self.settings.set("hotkeys", hk)
            self.settings.save()
            self._register_hotkeys()
        btn = self.hotkey_buttons.get(action)
        if btn:
            btn.config(text=self.settings.get("hotkeys", {}).get(action, "—").upper(), fg=FG)

    def _on_hotkey(self, action: str):
        if self.state not in ("MAIN", "BATTLE"):
            return
        if action == "battle_start" and self.state == "MAIN":
            if not self.settings.get("selected_tank"):
                self.main_status.config(text="Сначала выберите танк", fg=RED)
                return
            self.current_damage = 0
            self.watcher.reset_state()
            self.set_state("BATTLE")
        elif action in ("victory", "defeat") and self.state == "BATTLE":
            self._end_battle("victory" if action == "victory" else "defeat")

    def _apply_damage(self, value: int):
        """
        Применить число с плашки «Нанесён урон».

        Подпись у плашки за выстрел и у плашки с общим уроном за бой одинаковая,
        поэтому тип определяется величиной числа:

          * равно уже накопленному  -> общий урон повторяет наш счётчик,
                                       оставляем как есть (иначе удвоим);
          * больше накопленного И больше правдоподобного урона за выстрел
                                    -> это общий урон за бой, заменяем;
          * иначе                   -> урон за выстрел, прибавляем.

        Порог max_shot_damage обязателен: без него второй же выстрел, оказавшийся
        сильнее первого (300, затем 350), был бы принят за общий урон и обнулил бы
        накопленное. Значение должно быть выше максимального разового урона вашей
        техники — правьте в tank_stat_settings.txt.

        Побочный эффект полезен: плашка общего урона подтягивает счётчик, если
        какой-то выстрел был пропущен распознаванием.
        """
        cap = int(self.settings.get("max_shot_damage", 1200))
        if value == self.current_damage:
            log.info("Урон %d совпал с накопленным — плашка общего урона, не удваиваем", value)
        elif value > self.current_damage and value > cap:
            log.info("Урон %d принят за общий за бой (было %d)", value, self.current_damage)
            self.current_damage = value
        else:
            self.current_damage += value
            log.info("Урон за выстрел %d, за бой %d", value, self.current_damage)
        self.battle_damage.config(text=str(self.current_damage))

    def _end_battle(self, result: str):
        tank = self.settings.get("selected_tank") or "—"
        self.history.add_battle(tank, self.current_damage, result, self.session_id)
        log.info("Бой записан: %s, урон %d, %s", tank, self.current_damage, result)
        self.current_damage = 0
        self.set_state("MAIN")

    # ──────────────── события от потока распознавания ────────────────

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "damage":
                    if self.state == "BATTLE":
                        self._apply_damage(int(payload))
                elif kind == "hotkey":
                    self._on_hotkey(payload)
                elif kind == "bind":
                    self._apply_binding(payload[0], payload[1])
        except queue.Empty:
            pass
        except Exception as e:
            log.error("Ошибка обработки события: %s", e)
        self.root.after(100, self._poll_queue)

    # ──────────────── завершение ────────────────

    def shutdown(self):
        self.stop_event.set()
        try:
            self.settings.set("window_x", self.root.winfo_x())
            self.settings.set("window_y", self.root.winfo_y())
            self.settings.save()
        except Exception:
            pass
        try:
            import keyboard
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        if self.roi_editor is not None:
            try:
                self.roi_editor.close()
            except Exception:
                pass
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()
        log.info("Программа завершена")


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")
    app = OverlayApp()
    app.run()


if __name__ == "__main__":
    main()
