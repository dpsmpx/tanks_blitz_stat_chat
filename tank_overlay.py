# -*- coding: utf-8 -*-
"""
Оверлей статистики боёв (распознавание урона с экрана).
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
from roi_editor import (
    RoiEditor, font, BG, PANEL, FG, DIM, ACCENT, GREEN, RED, BLUE, FONT, SCREEN_SIZES,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("overlay")

# See repository history / local Drive copy for full OverlayApp implementation.
# Temporary bootstrap: open Drive folder or local artifacts for complete file.

class OverlayApp:
    def __init__(self):
        raise SystemExit(
            "Полный tank_overlay.py ещё загружается. "
            "Скопируйте файл из Google Drive в этот репозиторий."
        )

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")
    OverlayApp()

if __name__ == "__main__":
    main()
