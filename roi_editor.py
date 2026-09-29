# -*- coding: utf-8 -*-
"""ROI editor and shared UI constants for tank overlay."""
import tkinter as tk

BG = "#12151a"
PANEL = "#1b2029"
FG = "#e8eaed"
DIM = "#8b93a1"
ACCENT = "#ffb020"
GREEN = "#4ade80"
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
