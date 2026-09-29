# -*- coding: utf-8 -*-
"""
Распознавание плашек урона через EasyOCR.

В бою всплывают три вида плашек, и все они попадают в одну зону:

    «Нанесён урон 956»          — урон за выстрел          -> считаем
    «Нанесён урон 3 065»        — общий урон за бой        -> считаем (см. ниже)
    «Урон с вашей помощью 655»  — урон по засвету          -> игнорируем

Подпись у первых двух одинаковая, поэтому тип плашки определяется не текстом,
а величиной числа — решение принимает OverlayApp._apply_damage.

Здесь же решаются две практические задачи:
  * число «3 065» набрано с пробелом-разделителем и распознаётся как два
    фрагмента: они склеиваются по строке, а не берутся по максимуму
    (иначе max("3", "065") дал бы 65);
  * пустые кадры отсеиваются дешёвой проверкой на numpy до вызова OCR —
    EasyOCR слишком тяжёлый, чтобы гонять его вхолостую 8 раз в секунду.

Зависимости: numpy, mss, easyocr, Pillow
"""

import time
import queue
import difflib
import logging
import threading
from typing import Optional, List, Dict, Any

import numpy as np

log = logging.getLogger("recognition")

# ──────────────────────────── Подписи плашек ────────────────────────────

ASSIST_PHRASE = "уронсвашейпомощью"
DAMAGE_PHRASE = "нанесенурон"

# Ключевые куски: подпись может быть обрезана зоной или частично не распознана
ASSIST_KEYS = ("помощ", "вашей", "ашей")
DAMAGE_KEYS = ("нанес", "анесен")

# EasyOCR в кириллице часто отдаёт похожие латинские буквы — сворачиваем их
_HOMOGLYPHS = str.maketrans({
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "y": "у", "x": "х",
    "A": "А", "E": "Е", "O": "О", "P": "Р", "C": "С", "Y": "У", "X": "Х",
    "B": "В", "H": "Н", "T": "Т", "M": "М", "K": "К",
})

_SEPARATORS = " \u00a0\u2009\u202f,.'`’´"

# Частые подмены OCR внутри числа
_DIGIT_FIXES = str.maketrans({
    "O": "0", "o": "0", "О": "0", "о": "0", "D": "0", "Q": "0",
    "l": "1", "I": "1", "|": "1", "і": "1",
    "S": "5", "s": "5", "З": "3", "з": "3", "б": "6", "B": "8",
})


def _normalize_caption(text: str) -> str:
    """Подпись -> только буквы, нижний регистр, ё->е, латиница свёрнута в кириллицу."""
    text = text.translate(_HOMOGLYPHS).lower().replace("ё", "е")
    return "".join(ch for ch in text if ch.isalpha())


def classify_caption(caption: str) -> str:
    """
    Тип плашки по подписи. -> "assist" | "damage" | "unknown"

    Слово «урон» есть в обеих подписях, поэтому сначала проверяется ассист.
    """
    norm = _normalize_caption(caption)
    if not norm:
        return "unknown"
    if any(key in norm for key in ASSIST_KEYS):
        return "assist"
    if any(key in norm for key in DAMAGE_KEYS):
        return "damage"
    # подпись распозналась криво — сравниваем целиком
    if difflib.SequenceMatcher(None, norm, ASSIST_PHRASE).ratio() >= 0.62:
        return "assist"
    if difflib.SequenceMatcher(None, norm, DAMAGE_PHRASE).ratio() >= 0.62:
        return "damage"
    return "unknown"


def _digits_of(text: str) -> Optional[str]:
    """Строка -> только цифры, если это действительно число. Иначе None."""
    cleaned = "".join(ch for ch in text if ch not in _SEPARATORS)
    if not cleaned:
        return None
    fixed = cleaned.translate(_DIGIT_FIXES)
    return fixed if fixed.isdigit() else None


# ──────────────────────────── Быстрая проверка кадра ────────────────────────────

def has_bright_text(rgb: np.ndarray, white_threshold: int = 185, max_spread: int = 40,
                    min_fraction: float = 0.002, max_fraction: float = 0.65) -> bool:
    """
    Есть ли в зоне белый текст. Дешёвая проверка на numpy перед запуском OCR.

    Текст плашки белый: все каналы яркие и близки друг к другу. Цветной фон
    (небо, трава, техника) отсекается разбросом каналов.
    """
    if rgb is None or rgb.size == 0:
        return False
    arr = rgb.astype(np.int16)
    mn = arr.min(axis=2)
    mx = arr.max(axis=2)
    white = (mn >= white_threshold) & ((mx - mn) <= max_spread)
    fraction = float(white.mean())
    return min_fraction <= fraction <= max_fraction


# ──────────────────────────── EasyOCR ────────────────────────────

_reader = None
_reader_lock = threading.Lock()


def get_reader(languages: Optional[List[str]] = None):
    """Ленивая инициализация EasyOCR (один раз на процесс)."""
    global _reader
    with _reader_lock:
        if _reader is None:
            import easyocr
            langs = languages or ["ru", "en"]
            log.info("Инициализация EasyOCR (%s)...", "+".join(langs))
            # Русский нужен для подписи: по ней отсеивается «Урон с вашей помощью»
            _reader = easyocr.Reader(langs, gpu=False, verbose=False)
            log.info("EasyOCR готов")
        return _reader


def _group_lines(results: List[Any]) -> List[Dict[str, Any]]:
    """Фрагменты OCR -> строки текста (по вертикальному перекрытию рамок)."""
    items = []
    for entry in results:
        try:
            bbox, text = entry[0], entry[1]
        except (TypeError, IndexError):
            continue
        xs = [float(p[0]) for p in bbox]
        ys = [float(p[1]) for p in bbox]
        items.append({
            "text": str(text), "x0": min(xs), "x1": max(xs),
            "y0": min(ys), "y1": max(ys),
        })

    items.sort(key=lambda i: (i["y0"] + i["y1"]) / 2)
    lines: List[Dict[str, Any]] = []
    for it in items:
        placed = False
        for ln in lines:
            overlap = min(ln["y1"], it["y1"]) - max(ln["y0"], it["y0"])
            shorter = min(ln["y1"] - ln["y0"], it["y1"] - it["y0"]) or 1.0
            if overlap > 0.4 * shorter:
                ln["items"].append(it)
                ln["y0"] = min(ln["y0"], it["y0"])
                ln["y1"] = max(ln["y1"], it["y1"])
                placed = True
                break
        if not placed:
            lines.append({"y0": it["y0"], "y1": it["y1"], "items": [it]})

    for ln in lines:
        ln["items"].sort(key=lambda i: i["x0"])
        # фрагменты одной строки склеиваются: «3» + «065» -> «3 065»
        ln["text"] = " ".join(i["text"] for i in ln["items"])
        ln["height"] = ln["y1"] - ln["y0"]
    return lines


def parse_popup(results: List[Any]) -> Dict[str, Any]:
    """
    Результат OCR -> {"kind", "value", "caption"}.

    Число берётся из строки с самыми крупными цифрами (оно набрано крупнее
    подписи), фрагменты одной строки склеиваются по порядку слева направо.
    """
    lines = _group_lines(results)
    if not lines:
        return {"kind": "unknown", "value": None, "caption": "", "number_line": None}

    number_line = None
    caption_parts = []
    for ln in lines:
        digits = _digits_of(ln["text"])
        if digits:
            if number_line is None or ln["height"] > number_line["height"]:
                if number_line is not None:
                    caption_parts.append(number_line["text"])
                number_line = dict(ln, digits=digits)
            else:
                caption_parts.append(ln["text"])
        else:
            caption_parts.append(ln["text"])

    caption = " ".join(caption_parts).strip()
    value = None
    if number_line is not None:
        try:
            value = int(number_line["digits"])
        except ValueError:
            value = None

    return {
        "kind": classify_caption(caption),
        "value": value,
        "caption": caption,
        "number_line": number_line,
    }


def read_popup(rgb: np.ndarray, cfg: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """Прочитать плашку целиком. -> {"kind", "value", "caption"} или None, если пусто."""
    if rgb is None or rgb.size == 0:
        return None
    cfg = cfg or {}

    try:
        reader = get_reader()
        results = reader.readtext(rgb, detail=1, paragraph=False)
    except Exception as e:
        log.warning("Ошибка EasyOCR: %s", e)
        return None

    if not results:
        return None

    popup = parse_popup(results)

    # Подпись прочиталась, а число — нет: повторяем распознавание только по цифрам
    if popup["value"] is None and cfg.get("digit_retry", True):
        try:
            value = _retry_digits(rgb, reader)
            if value is not None:
                popup["value"] = value
        except Exception as e:
            log.debug("Повторное распознавание цифр не удалось: %s", e)

    if popup["value"] is None:
        return None
    return popup


def _retry_digits(rgb: np.ndarray, reader) -> Optional[int]:
    """Запасной проход: только цифры."""
    results = reader.readtext(rgb, allowlist="0123456789", detail=1, paragraph=False)
    lines = _group_lines(results)
    best = None
    for ln in lines:
        digits = _digits_of(ln["text"])
        if digits and (best is None or ln["height"] > best["height"]):
            best = dict(ln, digits=digits)
    if best is None:
        return None
    try:
        return int(best["digits"])
    except ValueError:
        return None


# ──────────────────────────── Наблюдатель зоны ────────────────────────────

class RoiWatcher(threading.Thread):
    """
    Фоновый поток: снимает ROI, читает плашку, отдаёт события в очередь.

    Режимы:
      idle      — ничего не делает
      battle    — читает плашки
      calibrate — то же, что idle (шаблоны больше не нужны)

    События в out_queue:
      ("damage", int)  — число с плашки «Нанесён урон».
                         Как его применить (заменить или прибавить),
                         решает OverlayApp._apply_damage.

    Плашки «Урон с вашей помощью» отбрасываются здесь и наверх не уходят.
    """

    POLL_INTERVAL = 0.12          # ~8 кадров в секунду

    def __init__(self, get_config, store, out_queue: "queue.Queue", stop_event: threading.Event):
        # store оставлен для совместимости с OverlayApp
        super().__init__(daemon=True, name="roi-watcher")
        self.get_config = get_config
        self.out = out_queue
        self.stop_event = stop_event

        self._mode = "idle"
        self._mode_lock = threading.Lock()

        # Дедупликация: одна плашка = одно попадание
        self._locked = False
        self._candidate: Optional[int] = None
        self._candidate_hits = 0
        self._last_committed: Optional[int] = None

    @property
    def mode(self) -> str:
        with self._mode_lock:
            return self._mode

    @mode.setter
    def mode(self, value: str):
        with self._mode_lock:
            if value != self._mode:
                self._mode = value
                self.reset_state()

    def reset_state(self):
        """Сброс дедупликации (начало боя / смена режима)."""
        self._locked = False
        self._candidate = None
        self._candidate_hits = 0
        self._last_committed = None

    def _on_empty_zone(self):
        """Плашка исчезла — снимаем блокировку, следующая будет засчитана."""
        self._locked = False
        self._candidate = None
        self._candidate_hits = 0

    def run(self):
        try:
            try:
                from mss import MSS as _MSS          # mss >= 10
            except ImportError:
                from mss import mss as _MSS          # mss < 10
        except ImportError:
            log.error("Модуль mss не установлен")
            return

        try:
            get_reader()
        except Exception as e:
            log.error("EasyOCR недоступен (%s) — распознавание отключено", e)
            return

        with _MSS() as sct:
            while not self.stop_event.is_set():
                if self.mode != "battle":
                    time.sleep(self.POLL_INTERVAL)
                    continue

                cfg = self.get_config()
                roi = cfg.get("roi") or {}
                w = int(roi.get("width", 0))
                h = int(roi.get("height", 0))
                if w < 10 or h < 8:
                    time.sleep(self.POLL_INTERVAL)
                    continue

                try:
                    frame = np.array(sct.grab({
                        "left": int(roi.get("x", 0)), "top": int(roi.get("y", 0)),
                        "width": w, "height": h,
                    }))
                except Exception as e:
                    log.warning("Ошибка захвата экрана: %s", e)
                    time.sleep(0.4)
                    continue

                rgb = frame[:, :, [2, 1, 0]]

                # Пустой кадр отсекаем без OCR — иначе впустую жжём процессор
                if cfg.get("skip_empty_frames", True) and not has_bright_text(
                    rgb,
                    white_threshold=int(cfg.get("white_threshold", 185)),
                    max_spread=int(cfg.get("max_spread", 40)),
                ):
                    self._on_empty_zone()
                    time.sleep(self.POLL_INTERVAL)
                    continue

                popup = read_popup(rgb, cfg)
                if popup is None:
                    self._on_empty_zone()
                    time.sleep(self.POLL_INTERVAL)
                    continue

                self._handle_popup(popup, cfg)
                time.sleep(self.POLL_INTERVAL)

    def _handle_popup(self, popup: Dict[str, Any], cfg: dict):
        kind = popup["kind"]
        value = popup["value"]

        if kind == "assist":
            # «Урон с вашей помощью» — чужая плашка, не наш урон
            self._candidate = None
            self._candidate_hits = 0
            log.info("Пропущен ассист: %s (%s)", value, popup.get("caption", ""))
            return

        if kind == "unknown" and not cfg.get("count_unknown_caption", False):
            # Подпись не прочиталась — не рискуем принять ассист за урон
            log.info("Подпись не распознана (%r, число %s) — плашка пропущена",
                     popup.get("caption", ""), value)
            return

        if self._locked:
            # Та же плашка ещё висит
            if cfg.get("count_on_change", False) and value != self._last_committed:
                self._commit(value, popup)
            return

        stable = int(cfg.get("stable_frames", 2))
        if value == self._candidate:
            self._candidate_hits += 1
        else:
            self._candidate = value
            self._candidate_hits = 1

        if self._candidate_hits >= stable:
            self._commit(value, popup)

    def _commit(self, value: int, popup: Dict[str, Any]):
        self._locked = True
        self._last_committed = value
        self._candidate = None
        self._candidate_hits = 0
        log.info("Плашка урона: %d (подпись: %r)", value, popup.get("caption", ""))
        self.out.put(("damage", int(value)))


# ──────────────────────────── Заглушки для совместимости ────────────────────────────

REQUIRED_DIGITS = [str(d) for d in range(10)]
GLYPH_W, GLYPH_H = 12, 20


class GlyphStore:
    """Заглушка: EasyOCR не использует шаблоны цифр."""

    def __init__(self, path: str = None, match_threshold: float = 0.8, max_variants: int = 6):
        self.path = path
        self.match_threshold = match_threshold

    def is_complete(self) -> bool:
        return True

    def resolution_matches(self, current) -> bool:
        return True

    def reset(self):
        pass

    def labelled_digits(self):
        return REQUIRED_DIGITS

    def missing_digits(self):
        return []
