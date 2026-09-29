# -*- coding: utf-8 -*-
"""
Хранение настроек и истории боёв.

  tank_stat_settings.txt — настройки по ТЗ (внутри JSON): ROI, хоткеи,
                           список танков, прозрачность, позиция окна.
  tank_history.json      — история боёв: танк, урон, результат, время.

Все записи атомарные; повреждённый файл не роняет программу, а уходит
в резервную копию, работа продолжается со значениями по умолчанию.
"""

import os
import json
import shutil
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

log = logging.getLogger("storage")

SETTINGS_FILE = "tank_stat_settings.txt"
HISTORY_FILE = "tank_history.json"
GLYPHS_FILE = "tank_stat_glyphs.json"

DEFAULT_SETTINGS: Dict[str, Any] = {
    # зона распознавания
    "roi": {"x": 800, "y": 400, "width": 200, "height": 60},
    # горячие клавиши (F-клавиши: игра их не использует)
    "hotkeys": {"battle_start": "f1", "victory": "f2", "defeat": "f3"},
    # танки
    "tanks": [],
    "selected_tank": None,
    # внешний вид
    "alpha": 0.85,
    "window_x": None,
    "window_y": None,
    # распознавание
    "invert": False,
    "min_contrast": 25.0,
    "min_glyph_w": 2,
    "min_glyph_h": 6,
    "stable_frames": 3,
    "match_threshold": 0.90,
    "count_on_change": False,
}


def _atomic_write(path: str, payload: Any) -> bool:
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        log.error("Не удалось записать %s: %s", path, e)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def _load_json(path: str, fallback: Any) -> Any:
    if not os.path.exists(path):
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        backup = f"{path}.corrupt-{datetime.now():%Y%m%d-%H%M%S}"
        log.error("Файл %s повреждён (%s). Копия: %s", path, e, backup)
        try:
            shutil.copy2(path, backup)
        except Exception:
            pass
        return fallback


class Settings:
    """Настройки приложения. Неизвестные поля из файла игнорируются."""

    def __init__(self, path: str = SETTINGS_FILE):
        self.path = path
        self.data: Dict[str, Any] = json.loads(json.dumps(DEFAULT_SETTINGS))
        self.load()

    def load(self):
        raw = _load_json(self.path, None)
        if not isinstance(raw, dict):
            self.save()
            return
        for key, default in DEFAULT_SETTINGS.items():
            value = raw.get(key, default)
            if isinstance(default, dict) and isinstance(value, dict):
                merged = dict(default)
                merged.update(value)
                self.data[key] = merged
            else:
                self.data[key] = value
        # список танков должен быть списком строк без дублей
        tanks = [str(t) for t in self.data.get("tanks", []) if str(t).strip()]
        seen, clean = set(), []
        for t in tanks:
            if t.lower() not in seen:
                seen.add(t.lower())
                clean.append(t)
        self.data["tanks"] = clean
        if self.data.get("selected_tank") not in clean:
            self.data["selected_tank"] = clean[0] if clean else None

    def save(self) -> bool:
        return _atomic_write(self.path, self.data)

    # доступ
    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any):
        self.data[key] = value

    # танки
    def add_tank(self, name: str) -> bool:
        name = (name or "").strip()
        if not name:
            return False
        tanks = self.data.setdefault("tanks", [])
        if any(t.lower() == name.lower() for t in tanks):
            return False
        tanks.append(name)
        if not self.data.get("selected_tank"):
            self.data["selected_tank"] = name
        self.save()
        return True

    def remove_tank(self, name: str):
        tanks = self.data.setdefault("tanks", [])
        self.data["tanks"] = [t for t in tanks if t != name]
        if self.data.get("selected_tank") == name:
            self.data["selected_tank"] = self.data["tanks"][0] if self.data["tanks"] else None
        self.save()

    def select_tank(self, name: Optional[str]):
        self.data["selected_tank"] = name
        self.save()


class History:
    """История боёв и производные метрики."""

    def __init__(self, path: str = HISTORY_FILE):
        self.path = path
        raw = _load_json(self.path, {"battles": []})
        if not isinstance(raw, dict) or not isinstance(raw.get("battles"), list):
            raw = {"battles": []}
        self.data: Dict[str, Any] = raw

    def save(self) -> bool:
        return _atomic_write(self.path, self.data)

    def add_battle(self, tank: str, damage: int, result: str, session_id: str) -> Dict[str, Any]:
        battle = {
            "tank": tank,
            "damage": int(damage),
            "result": result,               # "victory" | "defeat"
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "session_id": session_id,
        }
        self.data.setdefault("battles", []).append(battle)
        self.save()
        return battle

    def remove_last(self, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        battles = self.data.setdefault("battles", [])
        for i in range(len(battles) - 1, -1, -1):
            if session_id is None or battles[i].get("session_id") == session_id:
                removed = battles.pop(i)
                self.save()
                return removed
        return None

    # --- агрегаты ---
    @staticmethod
    def _stats(battles: List[Dict[str, Any]]) -> Dict[str, int]:
        n = len(battles)
        if n == 0:
            return {"battles": 0, "total_damage": 0, "avg_damage": 0, "win_rate": 0, "best_damage": 0}
        total = sum(int(b.get("damage", 0)) for b in battles)
        wins = sum(1 for b in battles if b.get("result") == "victory")
        return {
            "battles": n,
            "total_damage": total,
            "avg_damage": round(total / n),
            "win_rate": round(wins / n * 100),
            "best_damage": max(int(b.get("damage", 0)) for b in battles),
        }

    def session_stats(self, session_id: str) -> Dict[str, int]:
        return self._stats([b for b in self.data.get("battles", [])
                            if b.get("session_id") == session_id])

    def tank_stats(self, tank: str) -> Dict[str, int]:
        return self._stats([b for b in self.data.get("battles", [])
                            if str(b.get("tank", "")).lower() == str(tank).lower()])

    def last_battle(self, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        battles = self.data.get("battles", [])
        for b in reversed(battles):
            if session_id is None or b.get("session_id") == session_id:
                return b
        return None
