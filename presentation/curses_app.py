"""Точка входа презентационного слоя: главный цикл curses.

Управление (игра):
    Tab       -- переключение 2D / 3D режима
    WASD      -- движение
    (в 3D): W/S -- вперёд/назад, A/D -- поворот
    h         -- оружие (0 = снять)
    j         -- еда
    k         -- эликсир
    e         -- свиток
    q / Q     -- сохранить и выйти в меню
"""

from __future__ import annotations

import curses

from datalayer.storage import JsonDataLayer
from domain.engine import GameEngine
from domain.models import GameSession, ItemType
from presentation.input_handler import prompt_inventory_choice
from presentation.renderer import Renderer


class CursesApp:
    """Главный контроллер curses-приложения.

    Инициализирует ``Renderer`` при запуске и управляет переходами
    между экранами: главное меню - игра - таблица рекордов.

    Attributes:
        engine:     Игровой движок.
        data_layer: Слой данных — сохранение/загрузка сессий и рекордов.
    """

    def __init__(self, engine: GameEngine, data_layer: JsonDataLayer) -> None:
        self.engine = engine
        self.data_layer = data_layer
        self.first_person_mode: bool = False
        self.facing: int = 1  # 0=N, 1=E, 2=S, 3=W

    def run(self) -> None:
        """Запускает приложение внутри ``curses.wrapper``."""
        curses.wrapper(self._main)

    def _main(self, stdscr: curses.window) -> None:
        """Настраивает curses и запускает главный цикл меню.

        Args:
            stdscr (curses.window): Главное окно, переданное ``curses.wrapper``.
        """
        curses.curs_set(0)
        stdscr.nodelay(False)
        stdscr.keypad(True)

        renderer = Renderer(stdscr)

        while True:
            choice = self._menu(stdscr, renderer)

            if choice == "new":
                session = self.engine.new_session()
                self.first_person_mode = False
                self.facing = 1
                self._game_loop(stdscr, renderer, session)

            elif choice == "continue":
                session = self.data_layer.load_session()
                if session is None:
                    renderer.show_message(
                        "No saved session found. Press any key.")
                    continue
                self.first_person_mode = False
                self.facing = 1
                self._game_loop(stdscr, renderer,
                                self.engine.from_saved(session))

            elif choice == "records":
                renderer.show_records(self.data_layer.leaderboard())

            else:  # "quit"
                return

    def _menu(self, stdscr: curses.window, renderer: Renderer) -> str:
        """Отображает главное меню и возвращает выбранное действие.

        Поддерживает навигацию стрелками / WASD и прямой выбор цифрой.

        Args:
            stdscr (curses.window): Главное окно curses.
            renderer (Renderer): Рендерер (для ``_safe_addstr``).

        Returns:
            str: Одна из строк: ``"new"``, ``"continue"``, ``"records"``, ``"quit"``.
        """
        options = [
            ("new",      "1. New game"),
            ("continue", "2. Continue"),
            ("records",  "3. Leaderboard"),
            ("quit",     "4. Quit"),
        ]
        selected = 0

        while True:
            stdscr.erase()
            renderer.safe_addstr(
                1, 2, "Rogue 1980  (Python + curses)", curses.A_BOLD)
            renderer.safe_addstr(
                2, 2, "Arrows / WASD to navigate, Enter or digit to select")

            for idx, (_, label) in enumerate(options):
                attr = curses.A_REVERSE if idx == selected else curses.A_NORMAL
                renderer.safe_addstr(4 + idx, 4, label, attr)

            stdscr.refresh()
            key = stdscr.getch()

            if key in (curses.KEY_UP, ord("w"), ord("W")):
                selected = (selected - 1) % len(options)
            elif key in (curses.KEY_DOWN, ord("s"), ord("S")):
                selected = (selected + 1) % len(options)
            elif key in (curses.KEY_ENTER, 10, 13):
                return options[selected][0]
            elif key in (ord("1"), ord("2"), ord("3"), ord("4")):
                return options[key - ord("1")][0]

    def _game_loop(self, stdscr: curses.window, renderer: Renderer, session: GameSession) -> None:
        """Основной игровой цикл: рендер - ввод - команда - повтор.

        Цикл завершается при ``session.game_over`` (смерть или победа)
        или нажатии Esc (выход в меню).

        Args:
            stdscr (curses.window): Главное окно curses.
            renderer (Renderer): Рендерер кадра.
            session (GameSession): Текущая игровая сессия (мутируется движком).
        """
        while True:
            renderer.render_game(
                session,
                first_person=self.first_person_mode,
                facing=self.facing,
            )

            if session.game_over:
                self.data_layer.add_run_record(session.stats)
                self.data_layer.clear_session()
                ending = "Victory! You escaped the dungeon." if session.victory else "You died."
                renderer.show_message(f"{ending}  Press any key.")
                return

            key = stdscr.getch()
            if not self._handle_key(stdscr, renderer, session, key):
                return

    def _handle_key(self, stdscr: curses.window, renderer: Renderer,
                    session: GameSession, key: int) -> bool:
        """Маршрутизирует нажатие клавиши к соответствующей команде.

        Args:
            stdscr (curses.window): Главное окно curses.
            renderer (Renderer): Рендерер (нужен для диалога инвентаря).
            session (GameSession): Текущая игровая сессия.
            key (int): Код нажатой клавиши.
        """
        # -- Переключение режима --
        if key in (9, curses.KEY_BTAB):  # Tab / Shift+Tab
            self.first_person_mode = not self.first_person_mode
            session.message = "3D mode enabled." if self.first_person_mode else "2D mode enabled."
            return True

        # -- Движение / Поворот --
        if self.first_person_mode and key in (ord("w"), ord("W"), curses.KEY_UP):
            dx, dy = self._facing_vector()
            self.engine.move_player(session, dx, dy)
        elif self.first_person_mode and key in (ord("s"), ord("S"), curses.KEY_DOWN):
            dx, dy = self._facing_vector()
            self.engine.move_player(session, -dx, -dy)
        elif self.first_person_mode and key in (ord("a"), ord("A"), curses.KEY_LEFT):
            self.facing = (self.facing - 1) % 4
            session.message = "You turn left."
        elif self.first_person_mode and key in (ord("d"), ord("D"), curses.KEY_RIGHT):
            self.facing = (self.facing + 1) % 4
            session.message = "You turn right."
        elif key in (ord("w"), ord("W"), curses.KEY_UP):
            self.engine.move_player(session, 0, -1)
        elif key in (ord("s"), ord("S"), curses.KEY_DOWN):
            self.engine.move_player(session, 0, 1)
        elif key in (ord("a"), ord("A"), curses.KEY_LEFT):
            self.engine.move_player(session, -1, 0)
        elif key in (ord("d"), ord("D"), curses.KEY_RIGHT):
            self.engine.move_player(session, 1, 0)

        # -- Инвентарь --
        elif key == ord("h"):
            idx = prompt_inventory_choice(
                stdscr, renderer, session, ItemType.WEAPON, allow_zero=True,
            )
            if idx is not None:
                self.engine.use_item(session, ItemType.WEAPON, idx)

        elif key == ord("j"):
            idx = prompt_inventory_choice(
                stdscr, renderer, session, ItemType.FOOD)
            if idx is not None:
                self.engine.use_item(session, ItemType.FOOD, idx)

        elif key == ord("k"):
            idx = prompt_inventory_choice(
                stdscr, renderer, session, ItemType.ELIXIR)
            if idx is not None:
                self.engine.use_item(session, ItemType.ELIXIR, idx)

        elif key == ord("e"):
            idx = prompt_inventory_choice(
                stdscr, renderer, session, ItemType.SCROLL)
            if idx is not None:
                self.engine.use_item(session, ItemType.SCROLL, idx)

        # -- Сохранение и выход --
        elif key in (ord("q"), ord("Q")):
            self.engine.save_rng_state(session)
            self.data_layer.save_session(session)
            renderer.show_message("Game saved.  Press any key.")
            # Возвращаемся в _game_loop, он снова отрисует и продолжит
            # (или игрок нажмёт q снова — уже сохранено)

        # -- ESC: выход без сохранения --
        elif key == 27:
            renderer.show_message("Quit. Press any key.")
            return False

        return True

    def _facing_vector(self) -> tuple[int, int]:
        """Возвращает вектор шага для текущего направления взгляда."""
        return {
            0: (0, -1),   # N
            1: (1, 0),    # E
            2: (0, 1),    # S
            3: (-1, 0),   # W
        }[self.facing % 4]
