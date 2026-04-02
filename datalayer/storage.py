"""Слой хранения данных: сохранение сессии и таблица рекордов.

Весь файл сохранения — один JSON-файл со структурой:
    {
        "last_session": { ... } | null,
        "leaderboard":  [ { ... }, ... ]
    }
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from domain.models import GameSession, RunStats


# Максимальное количество записей в таблице рекордов.
_MAX_LEADERBOARD_SIZE: int = 200

# Структура файла сохранения по умолчанию (при отсутствии или повреждении).
_EMPTY_STORE: dict[str, Any] = {"last_session": None, "leaderboard": []}


class JsonDataLayer:
    """Слой хранения данных на основе одного JSON-файла.

    Attributes:
        path (Path): Путь к файлу сохранения.
    """

    def __init__(self, path: str = "savegame.json") -> None:
        """Инициализирует слой данных с указанным путём к файлу.

        Args:
            path (Path): Путь к JSON-файлу сохранения. Если файл не существует,
                  он будет создан при первой записи. Директория должна
                  существовать и быть доступна для записи.
        """
        self.path = Path(path)

    def save_session(self, session: GameSession) -> None:
        """Сохраняет текущую игровую сессию.

        Перезаписывает ``last_session`` в файле, сохраняя таблицу
        рекордов нетронутой.

        Args:
            session (GameSession): Сессия для сохранения. Должна содержать актуальный
                     ``rng_state`` (вызвать ``engine.save_rng_state``
                     перед этим методом).

        Raises:
            OSError: Если запись на диск невозможна (нет прав, нет места).
        """
        data = self._load_raw()
        data["last_session"] = session.to_dict()
        self._save_raw(data)

    def load_session(self) -> GameSession | None:
        """Загружает сохранённую сессию.

        Returns:
            GameSession | None: ``GameSession`` из файла или ``None`` 
            если сохранения нет либо оно повреждено.
        """
        raw = self._load_raw().get("last_session")
        if not raw:
            return None
        try:
            return GameSession.from_dict(raw)
        except (KeyError, ValueError, TypeError):
            # Повреждённые данные — возвращаем None, не крашим игру
            return None

    def clear_session(self) -> None:
        """Удаляет сохранённую сессию.

        Вызывается после завершения игры (смерть или победа), чтобы
        «Continue» в меню больше не предлагалось.
        """
        data = self._load_raw()
        data["last_session"] = None
        self._save_raw(data)

    # ------------------------------------------------------------------
    # Таблица рекордов
    # ------------------------------------------------------------------

    def add_run_record(self, stats: RunStats) -> None:
        """Добавляет запись о завершённом прохождении в таблицу рекордов.

        Таблица хранится отсортированной по убыванию ``treasure``.
        Не более ``_MAX_LEADERBOARD_SIZE``.

        Args:
            stats (RunStats): Статистика завершённого прохождения.
        """
        data = self._load_raw()
        leaderboard: list[dict] = data.get("leaderboard", [])
        leaderboard.append(stats.to_dict())
        leaderboard.sort(key=lambda x: int(x.get("treasure", 0)), reverse=True)
        data["leaderboard"] = leaderboard[:_MAX_LEADERBOARD_SIZE]
        self._save_raw(data)

    def leaderboard(self) -> list[RunStats]:
        """Возвращает таблицу рекордов, отсортированную по убыванию сокровищ.

        Returns:
            list[RunStats]: Список ``RunStats`` от лучшего к худшему. Пустой список
                                   если таблица пуста или файл отсутствует.
        """
        rows = self._load_raw().get("leaderboard", [])
        return [RunStats.from_dict(row) for row in rows]

    def _load_raw(self) -> dict[str, Any]:
        """Читает и парсит JSON-файл.

        Возвращает пустую структуру при отсутствии файла или
        невалидном JSON — так игра не крашится при повреждении файла.

        Returns:
            dict[str, Any]: Словарь с ключами ``"last_session"`` и ``"leaderboard"``.
        """
        if not self.path.exists():
            return dict(_EMPTY_STORE)
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return dict(_EMPTY_STORE)

    def _save_raw(self, data: dict[str, Any]) -> None:
        """Атомарно записывает данные в JSON-файл.

        Алгоритм:
        1. Записать данные во временный файл рядом с основным.
        2. ``os.replace`` — атомарная операция на POSIX (rename(2)).
           Если процесс упадёт до шага 2, основной файл не тронут.

        Args:
            data (dict[str, Any]): Данные для записи.

        Raises:
            OSError: Если директория недоступна для записи или нет места.
        """
        text = json.dumps(data, ensure_ascii=False, indent=2)
        tmp_path = self.path.with_suffix(".tmp")
        try:
            tmp_path.write_text(text, encoding="utf-8")
            os.replace(tmp_path, self.path)
        except OSError:
            # Убираем временный файл при ошибке, если он был создан
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise
