# -*- coding: utf-8 -*-
"""
Учёт статистики боёв World of Tanks Blitz по голосовым командам.

Архитектура (потоки):
  main      — Tkinter mainloop + обновление оверлея (after)
  listen    — захват аудио с микрофона -> audio_queue
  recog     — audio_queue -> Google STT -> command_queue
  cmd       — command_queue -> обработка команд
  tts       — tts_queue -> синтез речи (pyttsx3, офлайн)

Озвучка офлайн (pyttsx3), распознавание онлайн (Google STT).
Слой STT изолирован в _recognition_worker — заменяется на Vosk в одном месте.
"""

import os
import json
import csv
import time
import queue
import shutil
import difflib
import logging
import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Dict, Any

import speech_recognition as sr
import pyttsx3
import tkinter as tk

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tankstats")


# ──────────────────────────── Конфигурация ────────────────────────────

@dataclass
class AppConfig:
    # файлы
    data_file: str = "tank_stats.json"
    config_file: str = "config.json"
    pos_file: str = "window_pos.json"
    export_file: str = "tank_battles.csv"
    # окно
    width: int = 350
    height: int = 100
    alpha: float = 0.85
    bg: str = "#1a1a1a"
    font: str = "Segoe UI"
    font_size: int = 14
    # цвета
    color_tank: str = "#ffffff"
    color_time: str = "#ffff00"
    color_stat: str = "#00ff00"
    color_current: str = "#ff9900"
    # распознавание
    language: str = "ru-RU"
    energy_threshold: int = 4000
    dynamic_energy: bool = True
    pause_threshold: float = 0.8
    ambient_duration: float = 3.0
    phrase_time_limit: float = 3.0
    listen_timeout: float = 0.3
    # озвучка
    tts_rate: Optional[int] = None        # None -> скорость по умолчанию
    tts_voice_hint: str = "ru"            # подстрока для выбора русского голоса

    @classmethod
    def load(cls) -> "AppConfig":
        cfg = cls()
        path = cls.config_file
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for k, v in (raw or {}).items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
            else:
                # создаём файл с дефолтами, чтобы было что править руками
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(asdict(cfg), f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning("Конфиг %s не прочитан (%s) — беру значения по умолчанию", path, e)
        return cfg


# ──────────────────────────── Утилиты текста/чисел ────────────────────────────

def plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Склонение существительного при числительном (1 бой, 2 боя, 5 боёв)."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return few
    return many


def normalize(text: str) -> str:
    """Нижний регистр, ё->е, пунктуация -> пробел, схлопывание пробелов."""
    text = text.lower().replace("ё", "е")
    chars = (ch if (ch.isalnum() or ch.isspace()) else " " for ch in text)
    return " ".join("".join(chars).split())


_UNITS = {
    "ноль": 0, "один": 1, "одна": 1, "одну": 1, "два": 2, "две": 2,
    "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
    "восемь": 8, "девять": 9,
}
_TEENS = {
    "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13,
    "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16,
    "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
}
_TENS = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
}
_HUNDREDS = {
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400,
    "пятьсот": 500, "шестьсот": 600, "семьсот": 700,
    "восемьсот": 800, "девятьсот": 900,
}
_THOUSANDS = {"тысяча": 1000, "тысячи": 1000, "тысяч": 1000}

_WORD_NUM: Dict[str, int] = {}
for _d in (_UNITS, _TEENS, _TENS, _HUNDREDS):
    _WORD_NUM.update(_d)


def parse_number(text: str) -> Optional[int]:
    """Число из текста: цифрами ('250') или словами ('двести пятьдесят'). None — если чисел нет."""
    result = 0
    current = 0
    found = False
    for tok in normalize(text).split():
        if tok.isdigit():
            current += int(tok)
            found = True
        elif tok in _THOUSANDS:
            current = (current or 1) * 1000
            result += current
            current = 0
            found = True
        elif tok in _WORD_NUM:
            current += _WORD_NUM[tok]
            found = True
        # прочие слова игнорируем
    result += current
    return result if found else None


def extract_tank_name(raw: str) -> Optional[str]:
    """Если команда вида 'новая сессия <танк>' — вернуть имя танка (с исходным регистром)."""
    raw_tokens = raw.split()
    norm_tokens = [normalize(t) for t in raw_tokens]
    if "новая" not in norm_tokens:
        return None
    for i, nt in enumerate(norm_tokens):
        if nt.startswith("сес") or difflib.SequenceMatcher(None, nt, "сессия").ratio() >= 0.6:
            name = " ".join(raw_tokens[i + 1:]).strip(" .,!?-")
            return name or None
    return None


# ──────────────────────────── Оверлей ────────────────────────────

@dataclass
class StatsSnapshot:
    tank_name: str = "-"
    session_start: Optional[datetime] = None
    battle_count: int = 0
    avg_damage: int = 0
    win_rate: int = 0
    current_damage: int = 0


class OverlayWindow:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.root = tk.Tk()
        self._lock = threading.Lock()
        self._snap = StatsSnapshot()
        self.on_close = None            # колбэк завершения, ставится из main()
        self._x_off = 0
        self._y_off = 0
        self._dragging = False
        self._build_ui()
        self._load_position()

    def _build_ui(self):
        c = self.cfg
        self.root.title("Танки Статистика")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", c.alpha)
        self.root.configure(bg=c.bg)
        self.root.resizable(False, False)

        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = sw - c.width - 50, sh - c.height - 50
        self.root.geometry(f"{c.width}x{c.height}+{x}+{y}")

        self.container = tk.Frame(self.root, bg=c.bg)
        self.container.pack(expand=True, fill="both", padx=10, pady=5)
        for ev, fn in (("<ButtonPress-1>", self._start_move),
                       ("<ButtonRelease-1>", self._stop_move),
                       ("<B1-Motion>", self._do_move)):
            self.container.bind(ev, fn)

        # закрытие окна (нет системной рамки -> вешаем горячие клавиши)
        self.root.bind("<Escape>", lambda e: self._request_close())
        self.root.bind("<Control-q>", lambda e: self._request_close())

        f = (c.font, c.font_size, "bold")

        top = tk.Frame(self.container, bg=c.bg)
        top.pack(fill=tk.X, pady=(0, 5))
        self.tank_label = tk.Label(top, text="Танк: -", font=f, fg=c.color_tank, bg=c.bg)
        self.tank_label.pack(side=tk.LEFT)
        self.session_label = tk.Label(top, text="Время: 00:00:00", font=f, fg=c.color_time, bg=c.bg)
        self.session_label.pack(side=tk.RIGHT)

        mid = tk.Frame(self.container, bg=c.bg)
        mid.pack(fill=tk.X, pady=(0, 5))
        self.battles_label = tk.Label(mid, text="Бои: 0", font=f, fg=c.color_stat, bg=c.bg)
        self.battles_label.pack(side=tk.LEFT)
        self.winrate_label = tk.Label(mid, text="Win Rate: 0%", font=f, fg=c.color_stat, bg=c.bg)
        self.winrate_label.pack(side=tk.RIGHT)

        bot = tk.Frame(self.container, bg=c.bg)
        bot.pack(fill=tk.X)
        self.damage_label = tk.Label(bot, text="Ср. урон: 0", font=f, fg=c.color_stat, bg=c.bg)
        self.damage_label.pack(side=tk.LEFT)
        self.current_damage_label = tk.Label(bot, text="Урон: 0", font=f, fg=c.color_current, bg=c.bg)
        self.current_damage_label.pack(side=tk.RIGHT)   # БЫЛО НЕ УПАКОВАНО — исправлено

    # --- перемещение окна ---
    def _start_move(self, e):
        self._x_off, self._y_off, self._dragging = e.x, e.y, True

    def _stop_move(self, e):
        self._dragging = False
        self._save_position()

    def _do_move(self, e):
        if self._dragging:
            x = self.root.winfo_pointerx() - self._x_off
            y = self.root.winfo_pointery() - self._y_off
            self.root.geometry(f"+{x}+{y}")

    def _save_position(self):
        try:
            with open(self.cfg.pos_file, "w", encoding="utf-8") as fh:
                json.dump({"x": self.root.winfo_x(), "y": self.root.winfo_y()}, fh)
        except Exception as e:
            log.warning("Не удалось сохранить позицию окна: %s", e)

    def _load_position(self):
        path = self.cfg.pos_file
        if not os.path.exists(path) and os.path.exists("overlay_config.json"):
            path = "overlay_config.json"          # миграция со старой версии
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as fh:
                    p = json.load(fh)
                self.root.geometry(f"+{int(p.get('x', 100))}+{int(p.get('y', 100))}")
        except Exception as e:
            log.warning("Не удалось загрузить позицию окна: %s", e)

    # --- данные/отрисовка ---
    def update_stats(self, snap: StatsSnapshot):
        """Вызывается из других потоков: только подменяет снимок под локом."""
        with self._lock:
            self._snap = snap

    def _update_display(self):
        with self._lock:
            s = self._snap
        if s.session_start:
            secs = max(0, int((datetime.now() - s.session_start).total_seconds()))
            h, rem = divmod(secs, 3600)
            m, sec = divmod(rem, 60)
            t = f"{h:02d}:{m:02d}:{sec:02d}"
        else:
            t = "00:00:00"
        self.tank_label.config(text=f"Танк: {s.tank_name}")
        self.session_label.config(text=f"Время: {t}")
        self.battles_label.config(text=f"Бои: {s.battle_count}")
        self.winrate_label.config(text=f"Win Rate: {s.win_rate}%")
        self.damage_label.config(text=f"Ср. урон: {s.avg_damage}")
        self.current_damage_label.config(text=f"Урон: {s.current_damage}")
        self.root.after(1000, self._update_display)

    def _request_close(self):
        if callable(self.on_close):
            self.on_close()
        else:
            self.shutdown()

    def shutdown(self):
        self._save_position()
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self._update_display()
        try:
            self.root.mainloop()
        except Exception as e:
            log.error("Ошибка главного цикла окна: %s", e)


# ──────────────────────────── Запись статистики ────────────────────────────

class TankStatsRecorder:
    def __init__(self, overlay: OverlayWindow, cfg: AppConfig):
        self.cfg = cfg
        self.overlay = overlay
        self.data: Dict[str, Any] = {"sessions": []}
        self.current_session: Optional[Dict[str, Any]] = None
        self.current_battle_damage = 0

        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = cfg.energy_threshold
        self.recognizer.dynamic_energy_threshold = cfg.dynamic_energy
        self.recognizer.pause_threshold = cfg.pause_threshold

        self.stop_event = threading.Event()
        self.speaking = threading.Event()          # True, пока идёт озвучка
        self.audio_queue: "queue.Queue" = queue.Queue()
        self.command_queue: "queue.Queue[str]" = queue.Queue()
        self.tts_queue: "queue.Queue[str]" = queue.Queue()
        self._threads = []

        self._load_data()
        self._resume_session()
        self._update_overlay()

    # --- персистентность ---
    def _load_data(self):
        path = self.cfg.data_file
        if not os.path.exists(path):
            self.data = {"sessions": []}
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            if not isinstance(self.data, dict) or not isinstance(self.data.get("sessions"), list):
                raise ValueError("неверная структура файла")
        except Exception as e:
            backup = f"{path}.corrupt-{datetime.now():%Y%m%d-%H%M%S}"
            log.error("Файл данных повреждён (%s). Сохраняю копию %s, начинаю с пустого.", e, backup)
            try:
                shutil.copy2(path, backup)
            except Exception:
                pass
            self.data = {"sessions": []}

    def _save_data(self):
        path = self.cfg.data_file
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)                  # атомарная замена
        except Exception as e:
            log.error("Не удалось сохранить данные: %s", e)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def _resume_session(self):
        """Восстановить последнюю незавершённую сессию после перезапуска."""
        for s in reversed(self.data["sessions"]):
            if s.get("end_time") is None:
                self.current_session = s
                self.current_battle_damage = 0
                log.info("Возобновлена сессия: %s", s.get("tank_name", "-"))
                break

    # --- расчёты ---
    @staticmethod
    def _session_stats(s: Dict[str, Any]):
        n = int(s.get("battles_count", 0))
        v = int(s.get("victories_count", 0))
        dmg = int(s.get("total_damage", 0))
        wr = round(v / n * 100) if n else 0
        avg = round(dmg / n) if n else 0
        return n, v, dmg, wr, avg

    @staticmethod
    def _best_damage(s: Dict[str, Any]) -> int:
        return max((int(b.get("damage", 0)) for b in s.get("battles", [])), default=0)

    def _update_overlay(self):
        if self.current_session:
            n, v, dmg, wr, avg = self._session_stats(self.current_session)
            try:
                start = datetime.fromisoformat(self.current_session["start_time"])
            except Exception:
                start = None
            snap = StatsSnapshot(
                tank_name=self.current_session.get("tank_name", "-"),
                session_start=start,
                battle_count=n,
                avg_damage=avg,
                win_rate=wr,
                current_damage=self.current_battle_damage,
            )
        else:
            snap = StatsSnapshot()
        self.overlay.update_stats(snap)

    # --- озвучка (один поток-обработчик очереди) ---
    def speak(self, text: str):
        log.info("Озвучка: %s", text)
        self.tts_queue.put(text)

    def _tts_worker(self):
        try:
            engine = pyttsx3.init()
        except Exception as e:
            log.error("Синтез речи недоступен (%s). Озвучка отключена.", e)
            while not self.stop_event.is_set():       # просто опустошаем очередь
                try:
                    self.tts_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
            return

        if self.cfg.tts_rate:
            try:
                engine.setProperty("rate", self.cfg.tts_rate)
            except Exception:
                pass
        try:
            for voice in engine.getProperty("voices"):
                meta = f"{voice.id} {voice.name} {getattr(voice, 'languages', '')}".lower()
                if self.cfg.tts_voice_hint in meta or "russ" in meta:
                    engine.setProperty("voice", voice.id)
                    break
        except Exception:
            pass

        while not self.stop_event.is_set():
            try:
                text = self.tts_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            self.speaking.set()
            try:
                engine.say(text)
                engine.runAndWait()
            except Exception as e:
                log.error("Ошибка озвучивания: %s", e)
            finally:
                if self.tts_queue.empty():
                    time.sleep(0.15)                  # хвост звука не попадёт в микрофон
                    self.speaking.clear()
        try:
            engine.stop()
        except Exception:
            pass

    # --- распознавание ---
    def _listen_worker(self):
        try:
            mic = sr.Microphone()
        except Exception as e:
            log.error("Микрофон недоступен: %s", e)
            return
        with mic as source:
            log.info("Калибровка микрофона...")
            try:
                self.recognizer.adjust_for_ambient_noise(source, duration=self.cfg.ambient_duration)
            except Exception as e:
                log.warning("Калибровка не удалась: %s", e)
            log.info("Калибровка завершена. Слушаю.")
            while not self.stop_event.is_set():
                if self.speaking.is_set():
                    time.sleep(0.1)
                    continue
                try:
                    audio = self.recognizer.listen(
                        source,
                        timeout=self.cfg.listen_timeout,
                        phrase_time_limit=self.cfg.phrase_time_limit,
                    )
                    self.audio_queue.put(audio)
                except sr.WaitTimeoutError:
                    continue
                except Exception as e:
                    if not self.stop_event.is_set():
                        log.warning("Ошибка прослушивания: %s", e)

    def _recognition_worker(self):
        # Единственное место, где используется Google STT.
        # Для офлайна заменяется на Vosk здесь же.
        while not self.stop_event.is_set():
            try:
                audio = self.audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if self.speaking.is_set():
                continue                              # не распознаём собственную озвучку
            try:
                text = self.recognizer.recognize_google(audio, language=self.cfg.language)
                if text and text.strip():
                    log.info("Распознано: %s", text)
                    self.command_queue.put(text.strip())
            except sr.UnknownValueError:
                pass
            except sr.RequestError as e:
                log.warning("Сервис распознавания недоступен: %s", e)
            except Exception as e:
                log.warning("Ошибка распознавания: %s", e)

    def _command_worker(self):
        while not self.stop_event.is_set():
            try:
                cmd = self.command_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.process_command(cmd)
            except Exception as e:
                log.error("Ошибка обработки команды '%s': %s", cmd, e)

    # --- разбор команд ---
    def process_command(self, raw: str):
        if not raw:
            return
        cmd = normalize(raw)
        toks = cmd.split()

        def has(*keys) -> bool:
            return any(k in cmd for k in keys)

        if has("выход", "закрой программу", "закрыть программу", "заверши программу", "выключись"):
            self.request_shutdown()
            return
        if has("помощь", "справка"):
            self.show_help()
            return

        tank = extract_tank_name(raw)
        if tank:
            self.start_new_session(tank)
            return

        if has("отмена", "отменить", "отмени"):
            self.undo_last_battle()
            return
        if "минус" in toks:
            num = parse_number(raw)
            if num is not None:
                self.adjust_current_damage(-num)
                return
        if has("сброс", "сбросить", "обнули"):
            self.reset_current_damage()
            return
        if has("экспорт", "выгрузка", "выгрузи"):
            self.export_csv()
            return
        if has("итоги", "общая", "общий"):
            self.speak_lifetime_stats()
            return

        if "победа" in cmd:
            self.end_battle("victory")
            return
        if "поражение" in cmd:
            self.end_battle("defeat")
            return
        if "конец" in toks or "заверши сессию" in cmd or "закончить" in cmd:
            self.end_session()
            return
        if "статус" in cmd:
            self.speak_session_stats()
            return

        num = parse_number(raw)
        if num is not None:
            self.add_damage(num)
            return

        log.info("Игнорирую нераспознанную команду: %s", raw)

    # --- действия ---
    def start_new_session(self, tank_name: str):
        if self.current_session and self.current_session.get("end_time") is None:
            self.current_session["end_time"] = datetime.now().isoformat()   # тихо закрываем прошлую
        session = {
            "tank_name": tank_name,
            "start_time": datetime.now().isoformat(),
            "end_time": None,
            "battles": [],
            "total_damage": 0,
            "battles_count": 0,
            "victories_count": 0,
        }
        self.data["sessions"].append(session)
        self.current_session = session
        self.current_battle_damage = 0
        self._save_data()
        self.speak(f"Начата новая сессия для танка {tank_name}")
        self._update_overlay()

    def add_damage(self, damage: int):
        if not self.current_session:
            self.speak("Сначала начните новую сессию")
            return
        self.current_battle_damage += damage
        self.speak(f"Урон {damage}. За бой {self.current_battle_damage}")
        self._update_overlay()

    def adjust_current_damage(self, delta: int):
        if not self.current_session:
            self.speak("Нет активной сессии")
            return
        self.current_battle_damage = max(0, self.current_battle_damage + delta)
        self.speak(f"Урон за бой {self.current_battle_damage}")
        self._update_overlay()

    def reset_current_damage(self):
        if not self.current_session:
            self.speak("Нет активной сессии")
            return
        self.current_battle_damage = 0
        self.speak("Урон за бой сброшен")
        self._update_overlay()

    def end_battle(self, result: str):
        if not self.current_session:
            self.speak("Нет активной сессии")
            return
        battle = {
            "damage": self.current_battle_damage,
            "result": result,
            "timestamp": datetime.now().isoformat(),
        }
        s = self.current_session
        s["battles"].append(battle)
        s["total_damage"] = int(s.get("total_damage", 0)) + self.current_battle_damage
        s["battles_count"] = int(s.get("battles_count", 0)) + 1
        if result == "victory":
            s["victories_count"] = int(s.get("victories_count", 0)) + 1
        self.current_battle_damage = 0
        self._save_data()
        self.speak_session_stats()
        self._update_overlay()

    def undo_last_battle(self):
        s = self.current_session
        if not s or not s.get("battles"):
            self.speak("Нет боёв для отмены")
            return
        last = s["battles"].pop()
        s["total_damage"] = max(0, int(s.get("total_damage", 0)) - int(last.get("damage", 0)))
        s["battles_count"] = max(0, int(s.get("battles_count", 0)) - 1)
        if last.get("result") == "victory":
            s["victories_count"] = max(0, int(s.get("victories_count", 0)) - 1)
        self._save_data()
        self.speak("Последний бой отменён")
        self.speak_session_stats()
        self._update_overlay()

    def speak_session_stats(self):
        if not self.current_session:
            self.speak("Нет активной сессии")
            return
        n, v, dmg, wr, avg = self._session_stats(self.current_session)
        if n == 0:
            self.speak("В сессии пока не было боёв")
            return
        best = self._best_damage(self.current_session)
        self.speak(
            f"{n} {plural_ru(n, 'бой', 'боя', 'боёв')}. "
            f"Процент побед {wr} {plural_ru(wr, 'процент', 'процента', 'процентов')}. "
            f"Средний урон {avg}. Лучший бой {best}."
        )

    def end_session(self):
        if not self.current_session:
            self.speak("Нет активной сессии")
            return
        self.current_session["end_time"] = datetime.now().isoformat()
        n, v, dmg, wr, avg = self._session_stats(self.current_session)
        if n:
            self.speak(
                f"Сессия завершена. {n} {plural_ru(n, 'бой', 'боя', 'боёв')}, "
                f"побед {wr} {plural_ru(wr, 'процент', 'процента', 'процентов')}, "
                f"средний урон {avg}."
            )
        else:
            self.speak("Сессия завершена. Боёв не было.")
        self.current_session = None
        self.current_battle_damage = 0
        self._save_data()
        self._update_overlay()

    def speak_lifetime_stats(self):
        tank = self.current_session.get("tank_name") if self.current_session else None
        sessions = self.data.get("sessions", [])
        if tank:
            subset = [s for s in sessions if s.get("tank_name", "").lower() == tank.lower()]
            label = f"по танку {tank}"
        else:
            subset = sessions
            label = "за всё время"
        n = sum(int(s.get("battles_count", 0)) for s in subset)
        v = sum(int(s.get("victories_count", 0)) for s in subset)
        dmg = sum(int(s.get("total_damage", 0)) for s in subset)
        if n == 0:
            self.speak(f"Нет данных {label}")
            return
        wr, avg = round(v / n * 100), round(dmg / n)
        self.speak(
            f"Итого {label}: {n} {plural_ru(n, 'бой', 'боя', 'боёв')}, "
            f"побед {wr} {plural_ru(wr, 'процент', 'процента', 'процентов')}, "
            f"средний урон {avg}."
        )

    def export_csv(self):
        path = self.cfg.export_file
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Танк", "Начало сессии", "Время боя", "Урон", "Результат"])
                for s in self.data.get("sessions", []):
                    tank = s.get("tank_name", "")
                    start = s.get("start_time", "")
                    for b in s.get("battles", []):
                        res = "Победа" if b.get("result") == "victory" else "Поражение"
                        w.writerow([tank, start, b.get("timestamp", ""), b.get("damage", 0), res])
            log.info("Экспорт: %s", os.path.abspath(path))
            self.speak("Статистика выгружена в файл")
        except Exception as e:
            log.error("Ошибка экспорта: %s", e)
            self.speak("Не удалось выгрузить статистику")

    def show_help(self):
        help_text = (
            "\nДОСТУПНЫЕ КОМАНДЫ:\n"
            "  Новая сессия <танк>   — начать сессию\n"
            "  <число>               — добавить урон (цифрами или словами)\n"
            "  минус <число>         — вычесть из урона текущего боя\n"
            "  сброс                 — обнулить урон текущего боя\n"
            "  победа / поражение    — завершить бой\n"
            "  отмена                — удалить последний бой\n"
            "  статус                — статистика текущей сессии\n"
            "  итоги                 — статистика по танку за всё время\n"
            "  экспорт               — выгрузить всё в CSV\n"
            "  конец                 — завершить сессию\n"
            "  выход                 — закрыть программу\n"
            "  помощь                — эта справка\n"
            "Окно: перетаскивание ЛКМ, закрыть — Esc или Ctrl+Q.\n"
        )
        print(help_text)
        self.speak("Справка показана в консоли")

    # --- жизненный цикл ---
    def request_shutdown(self):
        log.info("Завершение работы")
        self.stop_event.set()
        self._save_data()
        try:
            self.overlay.root.after(0, self.overlay.shutdown)   # закрытие окна — в главном потоке
        except Exception:
            pass

    def start(self):
        self.speak("Программа учёта статистики запущена")
        print("\nПрограмма готова. Скажите 'Помощь' для списка команд.")
        self._threads = [
            threading.Thread(target=self._tts_worker, daemon=True, name="tts"),
            threading.Thread(target=self._listen_worker, daemon=True, name="listen"),
            threading.Thread(target=self._recognition_worker, daemon=True, name="recog"),
            threading.Thread(target=self._command_worker, daemon=True, name="cmd"),
        ]
        for t in self._threads:
            t.start()


def main():
    cfg = AppConfig.load()
    overlay = OverlayWindow(cfg)
    recorder = TankStatsRecorder(overlay, cfg)
    overlay.on_close = recorder.request_shutdown
    recorder.start()
    overlay.run()          # блокирующий вызов до закрытия окна
    log.info("Программа завершена")


if __name__ == "__main__":
    main()
