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

# NOTE: full source restored from Drive; if truncated, re-upload from artifacts.
# See Google Drive file tank_overlay.py for complete OverlayApp implementation.

BG = "#12151a"
PANEL = "#1b2029"
FG = "#e8eaed"
DIM = "#8b93a1"
ACCENT = "#ffb020"
GREEN = "#4ade80"
RED = "#f87171"
BLUE = "#60a5fa"
FONT = "Segoe UI"

def font(size: int, bold: bool = False) -> tuple:
    return (FONT, size, "bold") if bold else (FONT, size)

class OverlayApp:
    def __init__(self):
        raise SystemExit(
            "Полный tank_overlay.py нужно восстановить из Google Drive.\n"
            "Файл: https://drive.google.com/file/d/1olypKei_g-6nhoA3LtRujatsIqOr6241/view"
        )

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")
    OverlayApp()

if __name__ == "__main__":
    main()
