"""Обработчик пользовательского ввода инвентаря.

Модуль отвечает за интерактивный диалог выбора предмета из рюкзака.
"""
# pylint: disable=no-member
from __future__ import annotations

import curses

from domain.models import GameSession, ItemType
from presentation.renderer import Renderer


def prompt_inventory_choice(stdscr: curses.window, renderer: Renderer, session: GameSession,
                            item_type: ItemType, allow_zero: bool = False) -> int | None:
    """Интерактивно запрашивает выбор предмета из рюкзака.

    Отображает список предметов выбранного типа и ждёт нажатия цифры.
    Нажатие ESC, q или Q отменяет выбор.

    Args:
        stdscr (curses.window): Главное окно curses.
        renderer (Renderer): Рендерер — используется для безопасного вывода текста.
        session (GameSession): Текущая игровая сессия.
        item_type (ItemType): Тип запрашиваемого предмета.
        allow_zero (bool): Если ``True`` — принимается «0» для снятия оружия.

    Returns:
        int | None: Выбранный 1-based индекс предмета, 0 для «снять оружие»,
                    или ``None`` если выбор отменён.
    """
    items = session.backpack.bucket(item_type)
    has_equipped_weapon = item_type == ItemType.WEAPON and session.hero.weapon is not None

    if not items and not (allow_zero and has_equipped_weapon):
        session.message = f"No {item_type.value} in backpack."
        return None

    _draw_inventory_dialog(stdscr, renderer, session,
                           item_type, items, allow_zero)

    return _read_inventory_key(stdscr, items, allow_zero)


def _draw_inventory_dialog(stdscr: curses.window, renderer: Renderer, session: GameSession,
                           item_type: ItemType, items: list, allow_zero: bool) -> None:
    """Отрисовывает диалог выбора предмета снизу игровой карты.

    Args:
        stdscr (curses.window): Главное окно curses.
        renderer (Renderer): Рендерер (``safe_addstr``).
        session (GameSession): Текущая сессия.
        item_type (ItemType): Тип предмета (для заголовка).
        items (list): Список предметов этого типа.
        allow_zero (bool): Показывать ли строку «0. Unequip».
    """
    level = session.level
    y0 = min(level.height + 5, curses.LINES - 14)

    # Очищаем область диалога
    stdscr.move(y0, 0)
    stdscr.clrtobot()

    digits = "0-9" if allow_zero else "1-9"
    title = f"Choose {item_type.value} ({digits}), Q to cancel:"
    renderer.safe_addstr(y0, 0, title, curses.A_BOLD)

    row = y0 + 1
    if allow_zero:
        current = session.hero.weapon
        weapon_name = f"{current.name} (+{current.value})" if current else "none"
        renderer.safe_addstr(
            row, 2, f"0. Unequip current weapon [{weapon_name}]")
        row += 1

    for idx, item in enumerate(items[:9], start=1):
        desc = f"{idx}. {item.name}"
        if item.stat is not None:
            desc += f" [{item.stat.value} +{item.value}]"
        elif item.value:
            desc += f" [+{item.value}]"
        if item.duration:
            desc += f" ({item.duration} turns)"
        renderer.safe_addstr(row, 2, desc)
        row += 1

    stdscr.refresh()


def _read_inventory_key(stdscr: curses.window, items: list, allow_zero: bool) -> int | None:
    """Читает нажатие клавиши и возвращает выбранный индекс.

    Args:
        stdscr (curses.window): Главное окно curses.
        items (list): Список предметов (для проверки допустимости индекса).
        allow_zero (bool): Принимать ли «0».

    Returns:
        int | None: Индекс (0–9) или ``None`` при отмене.
    """
    while True:
        key = stdscr.getch()
        if key in (27, ord("q"), ord("Q")):   # ESC или q — отмена
            return None
        if ord("0") <= key <= ord("9"):
            idx = key - ord("0")
            if idx == 0 and allow_zero:
                return 0
            if 1 <= idx <= len(items):
                return idx
