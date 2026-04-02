"""Рендерер игры на базе curses.

Модуль отвечает исключительно за отрисовку состояния игровой сессии в терминале.

Архитектура рендерера:
    Renderer хранит ссылку на ``stdscr`` и предоставляет метод
    ``render_game(session)`` — единственную точку входа для
    основного игрового вида. Вспомогательные представления
    (таблица рекордов, временные сообщения) реализованы отдельными
    методами.

Порядок отрисовки за один кадр (``render_game``):
    1. Тайлы карты с учётом тумана войны   (_render_tiles)
    2. Предметы на полу                     (_render_items)
    3. Враги (с учётом невидимости Ghost)   (_render_enemies)
    4. Герой                                (_render_hero)
    5. HUD: статус, инвентарь, подсказки    (_render_hud)

Туман войны:
    Рендерер читает ``session.last_visible`` — кеш видимых клеток,
    вычисленный движком в конце последнего хода.

Цветовая схема (индексы curses color_pair):
    1=RED  2=GREEN  3=YELLOW  4=CYAN  5=WHITE  6=BLUE  7=MAGENTA
"""
# pylint: disable=no-member

from __future__ import annotations

import curses
import math

from domain.models import EnemyType, GameSession, Item, ItemType, Pos, RunStats, Tile, Level


# Минимальный размер терминала для корректного отображения
_MIN_LINES: int = 24
_MIN_COLS:  int = 80
_HUD_OFFSET: int = 1  # Отступ строк HUD от нижней границы карты


class Renderer:
    """Рендерер игры: отрисовывает состояние сессии средствами curses.

    Хранит ссылку на ``stdscr`` и инкапсулирует все вызовы curses API.
    Инициализация цветов выполняется один раз при создании экземпляра.

    Attributes:
        stdscr (curses.window): Главное окно curses, переданное из ``CursesApp``.
    """

    def __init__(self, stdscr: curses.window) -> None:
        """Инициализирует рендерер и цветовые пары curses.

        Args:
            stdscr: Главное окно curses (``curses.wrapper`` передаёт его
                    в ``_main``).
        """
        self.stdscr = stdscr
        self._init_colors()

    def render_game(
        self,
        session: GameSession,
        first_person: bool = False,
        facing: int = 1,
    ) -> None:
        """Отрисовывает полный игровой кадр: карту, акторов, HUD.

        Использует ``session.last_visible`` как кеш видимых клеток —
        он вычисляется движком в конце каждого хода.

        Args:
            session (GameSession): Текущая игровая сессия (только читается).
            first_person (bool): ``True`` — режим 3D от первого лица.
            facing (int): Направление взгляда в формате ``0=N, 1=E, 2=S, 3=W``.
        """
        if not self._check_terminal_size():
            return

        self.stdscr.erase()
        if first_person:
            self._render_3d(session, facing)
            self._render_hud(
                session,
                mode_label="3D",
                facing=facing,
            )
        else:
            level = session.level
            visible = session.last_visible

            self._render_tiles(session, visible)
            self._render_items(level, visible)
            self._render_enemies(level, visible)
            self._render_hero(session)
            self._render_hud(session, mode_label="2D", facing=facing)
        self.stdscr.refresh()

    def _render_3d(self, session: GameSession, facing: int) -> None:
        """Отрисовывает 3D-вид от первого лица через ray casting.

        Args:
            session (GameSession): Текущая сессия.
            facing (int): Направление взгляда ``0=N, 1=E, 2=S, 3=W``.
        """
        level = session.level
        hero = session.hero_pos
        view_w = max(34, min(curses.COLS - 2, 110))
        view_h = max(14, min(curses.LINES - 10, 36))
        ox = 1
        oy = 1

        # Рамка вокруг
        self._draw_box(0, 0, view_h + 2, view_w + 2, " First Person ")

        # небо/пол
        horizon = view_h // 2
        for y in range(view_h):
            for x in range(view_w):
                if y < horizon:
                    t = (horizon - y) / max(1, horizon)
                    ch = " " if t > 0.55 else "."
                    attr = curses.color_pair(7) | curses.A_DIM
                else:
                    t = (y - horizon) / max(1, view_h - horizon)
                    if t < 0.20:
                        ch = "."
                    elif t < 0.55:
                        ch = ","
                    else:
                        ch = ";"
                    attr = curses.color_pair(4) | curses.A_DIM
                self.safe_addstr(oy + y, ox + x, ch, attr)

        dir_angles = [-math.pi / 2, 0.0, math.pi / 2, math.pi]
        dir_angle = dir_angles[facing % 4]
        # сейчас fov установленна на 60
        fov = math.pi / 3
        depth_buffer: list[float] = [24.0 for _ in range(view_w)]

        for col in range(view_w):
            ray_angle = dir_angle - fov / 2 + (col / max(1, view_w - 1)) * fov
            dist, hit_tile = self._cast_3d_ray(level, hero, ray_angle)
            corrected = max(0.12, dist * math.cos(ray_angle - dir_angle))
            depth_buffer[col] = corrected

            wall_h = min(view_h, int(view_h / corrected))
            top = max(0, (view_h - wall_h) // 2)
            bottom = min(view_h - 1, top + wall_h)
            wall_char = self._wall_texture(corrected, hit_tile)
            wall_attr = self._wall_color(hit_tile, corrected)

            for y in range(top, bottom + 1):
                draw_ch = wall_char
                if (y - top) % 2 == 1 and corrected > 4.5:
                    draw_ch = wall_char.lower() if wall_char.isalpha() else wall_char
                self.safe_addstr(oy + y, ox + col, draw_ch, wall_attr)

        self._render_3d_sprites(session, ox, oy, view_w,
                                view_h, dir_angle, fov, depth_buffer)

        cross_x = ox + view_w // 2
        cross_y = oy + view_h // 2
        self.safe_addstr(cross_y, cross_x, "+",
                         curses.color_pair(5) | curses.A_BOLD)

        # Мини карта
        mini_w = min(22, max(14, curses.COLS // 4))
        mini_h = min(12, max(8, curses.LINES // 5))
        mini_w = min(mini_w, max(10, view_w - 4))
        mini_h = min(mini_h, max(6, view_h - 4))
        mini_x = max(2, view_w - mini_w)
        mini_y = 0
        self._draw_box(mini_y, mini_x, mini_h + 2, mini_w + 2, " Map ")
        self._draw_minimap(
            session,
            facing,
            mini_x + 1,
            mini_y + 1,
            mini_w,
            mini_h,
        )

    def _cast_3d_ray(self, level: Level, hero_pos: Pos, angle: float) -> tuple[float, str | None]:
        """Пускает луч до первой стены/двери

        Returns:
            tuple[float, str | None]:
                дистанция и тип препятствия (``"#"``, ``"R"``, ``"B"``, ``"Y"``, либо ``None``).
        """
        max_depth = 24.0
        step = 0.08
        x = hero_pos.x + 0.5
        y = hero_pos.y + 0.5
        dist = 0.0

        while dist < max_depth:
            dist += step
            x += math.cos(angle) * step
            y += math.sin(angle) * step
            tx = int(x)
            ty = int(y)
            pos = Pos(tx, ty)

            if not level.is_inside(pos):
                return max_depth, None

            tile = level.tile_at(pos)
            if tile in {
                Tile.WALL.value,
                Tile.DOOR_RED.value,
                Tile.DOOR_BLUE.value,
                Tile.DOOR_YELLOW.value,
            }:
                return dist, tile

        return max_depth, None

    def _wall_texture(self, distance: float, hit_tile: str | None) -> str:
        """Подбирает стену по расстоянию от игрока"""
        if hit_tile in {Tile.DOOR_RED.value, Tile.DOOR_BLUE.value, Tile.DOOR_YELLOW.value}:
            if distance < 2.3:
                return "H"
            if distance < 5.0:
                return "I"
            return "|"

        shades = "@#%*+=-:."
        idx = min(len(shades) - 1, int(distance * 0.8))
        return shades[idx]

    def _wall_color(self, hit_tile: str | None, distance: float) -> int:
        """Цвет колонки"""
        if hit_tile == Tile.DOOR_RED.value:
            attr = curses.color_pair(1) | curses.A_BOLD
        elif hit_tile == Tile.DOOR_BLUE.value:
            attr = curses.color_pair(6) | curses.A_BOLD
        elif hit_tile == Tile.DOOR_YELLOW.value:
            attr = curses.color_pair(3) | curses.A_BOLD
        else:
            if distance < 3.0:
                attr = curses.color_pair(6) | curses.A_BOLD
            elif distance < 6.0:
                attr = curses.color_pair(6)
            else:
                attr = curses.color_pair(6) | curses.A_DIM
        if distance > 8.0:
            attr |= curses.A_DIM
        return attr

    def _render_3d_sprites(
        self,
        session: GameSession,
        ox: int,
        oy: int,
        view_w: int,
        view_h: int,
        dir_angle: float,
        fov: float,
        depth_buffer: list[float],
    ) -> None:
        """Отрисовывает врагов и предметы"""
        level = session.level
        hero_x = session.hero_pos.x + 0.5
        hero_y = session.hero_pos.y + 0.5
        half_fov = fov / 2

        sprites: list[tuple[float, float, str, int, float]] = []

        for enemy in level.enemies.values():
            if enemy.enemy_type == EnemyType.GHOST and not enemy.ghost_visible:
                continue
            dx = enemy.pos.x + 0.5 - hero_x
            dy = enemy.pos.y + 0.5 - hero_y
            raw_dist = math.hypot(dx, dy)
            if raw_dist < 0.35 or raw_dist > 24.0:
                continue
            rel = self._norm_angle(math.atan2(dy, dx) - dir_angle)
            if abs(rel) > half_fov + 0.14:
                continue
            center_ray_dist, _ = self._cast_3d_ray(
                level, session.hero_pos, dir_angle + rel)
            corrected = max(0.10, raw_dist * math.cos(rel))
            if corrected > center_ray_dist + 0.15:
                continue

            symbol = enemy.symbol
            color = curses.color_pair(enemy.color)

            if enemy.enemy_type == EnemyType.MIMIC and enemy.disguised:
                symbol = enemy.mimic_item_symbol

            sprites.append((corrected, rel, symbol, color | curses.A_BOLD, 1.0))

        for (x, y), item in level.items.items():
            dx = x + 0.5 - hero_x
            dy = y + 0.5 - hero_y
            raw_dist = math.hypot(dx, dy)
            if raw_dist < 0.35 or raw_dist > 24.0:
                continue
            rel = self._norm_angle(math.atan2(dy, dx) - dir_angle)
            if abs(rel) > half_fov + 0.14:
                continue
            center_ray_dist, _ = self._cast_3d_ray(
                level, session.hero_pos, dir_angle + rel)
            corrected = max(0.10, raw_dist * math.cos(rel))
            if corrected > center_ray_dist + 0.10:
                continue
            sym, attr = self._item_symbol(item)
            sprites.append((corrected, rel, sym, attr | curses.A_BOLD, 0.55))

        # Выход на следующий уровень рендерим как отдельный
        exit_x, exit_y = level.exit_pos.x, level.exit_pos.y
        if (exit_x, exit_y) != (session.hero_pos.x, session.hero_pos.y):
            dx = exit_x + 0.5 - hero_x
            dy = exit_y + 0.5 - hero_y
            raw_dist = math.hypot(dx, dy)
            if 0.35 < raw_dist <= 24.0:
                rel = self._norm_angle(math.atan2(dy, dx) - dir_angle)
                if abs(rel) <= half_fov + 0.14:
                    center_ray_dist, _ = self._cast_3d_ray(
                        level, session.hero_pos, dir_angle + rel)
                    corrected = max(0.10, raw_dist * math.cos(rel))
                    if corrected <= center_ray_dist + 0.10:
                        sprites.append(
                            (corrected, rel, ">", curses.color_pair(5) | curses.A_BOLD, 0.62))

        sprites.sort(reverse=True, key=lambda s: s[0])

        for dist, rel, sym, attr, scale in sprites:
            center_col = int(((rel + half_fov) / fov) * (view_w - 1))
            sprite_h = max(
                1, min(view_h - 2, int((view_h / max(0.22, dist)) * scale)))
            sprite_w = 1 if dist > 2.2 else 2
            top = max(0, view_h // 2 - sprite_h //
                      2 + (2 if scale < 1.0 else 0))
            bottom = min(view_h - 1, top + sprite_h - 1)
            left = center_col - sprite_w // 2
            right = left + sprite_w - 1

            for col in range(left, right + 1):
                if col < 0 or col >= view_w:
                    continue
                if dist >= depth_buffer[col] - 0.05:
                    continue
                draw_attr = attr | (curses.A_DIM if dist > 7.0 else 0)
                for row in range(top, bottom + 1):
                    self.safe_addstr(oy + row, ox + col, sym, draw_attr)
                depth_buffer[col] = dist

    def _draw_minimap(
        self,
        session: GameSession,
        facing: int,
        x0: int,
        y0: int,
        width: int,
        height: int,
    ) -> None:
     # Тут позиция миникарты
        level = session.level
        visible = session.last_visible

        half_w = width // 2
        half_h = height // 2

        for sy in range(height):
            my = session.hero_pos.y - half_h + sy
            for sx in range(width):
                mx = session.hero_pos.x - half_w + sx
                pos = Pos(mx, my)

                if not level.is_inside(pos):
                    self.safe_addstr(y0 + sy, x0 + sx, " ")
                    continue

                room = level.room_for(pos)
                seen = (
                    (mx, my) in visible
                    or (mx, my) in level.discovered_corridors
                    or (room is not None and room.discovered)
                )
                if not seen:
                    self.safe_addstr(y0 + sy, x0 + sx, " ")
                    continue

                ch = "."
                attr = curses.color_pair(4) | curses.A_DIM
                tile = level.tile_at(pos)

                if tile == Tile.WALL.value:
                    ch = "#"
                    attr = curses.color_pair(6)
                elif tile == Tile.EXIT.value:
                    ch = ">"
                    attr = curses.color_pair(5)
                elif tile == Tile.DOOR_RED.value:
                    ch = "R"
                    attr = curses.color_pair(1) | curses.A_BOLD
                elif tile == Tile.DOOR_BLUE.value:
                    ch = "B"
                    attr = curses.color_pair(6) | curses.A_BOLD
                elif tile == Tile.DOOR_YELLOW.value:
                    ch = "Y"
                    attr = curses.color_pair(3) | curses.A_BOLD

                item = level.items.get((mx, my))
                if item is not None and (mx, my) in visible:
                    ch, attr = self._item_symbol(item)

                enemy = next((e for e in level.enemies.values()
                             if e.pos.x == mx and e.pos.y == my), None)
                if enemy is not None and (mx, my) in visible:
                    if not (enemy.enemy_type == EnemyType.GHOST and not enemy.ghost_visible):
                        if enemy.enemy_type == EnemyType.MIMIC and enemy.disguised:
                            ch = enemy.mimic_item_symbol
                            attr = curses.color_pair(5)
                        else:
                            ch = enemy.symbol
                            attr = curses.color_pair(enemy.color)
                if (mx, my) == (session.hero_pos.x, session.hero_pos.y):
                    ch = ["^", ">", "v", "<"][facing % 4]
                    attr = curses.color_pair(5) | curses.A_BOLD

                self.safe_addstr(y0 + sy, x0 + sx, ch, attr)

    def _draw_box(
        self,
        y: int,
        x: int,
        h: int,
        w: int,
        title: str = "",
    ) -> None:
        if h < 2 or w < 2:
            return
        for cx in range(x, x + w):
            self.safe_addstr(y, cx, "-")
            self.safe_addstr(y + h - 1, cx, "-")
        for cy in range(y, y + h):
            self.safe_addstr(cy, x, "|")
            self.safe_addstr(cy, x + w - 1, "|")
        self.safe_addstr(y, x, "+")
        self.safe_addstr(y, x + w - 1, "+")
        self.safe_addstr(y + h - 1, x, "+")
        self.safe_addstr(y + h - 1, x + w - 1, "+")
        if title:
            self.safe_addstr(y, x + 2, title[: max(0, w - 4)], curses.A_BOLD)

    def _norm_angle(self, angle: float) -> float:
        """Нормализует угол в диапазон ``[-pi, pi]``"""
        while angle <= -math.pi:
            angle += 2 * math.pi
        while angle > math.pi:
            angle -= 2 * math.pi
        return angle

    def _render_tiles(self, session: GameSession, visible: set[tuple[int, int]]) -> None:
        """Отрисовывает тайлы карты с учётом тумана войны

        Правила тумана войны:
        - Неизведанные комнаты (``room.discovered == False``) — пробел.
        - Просмотренные, но не текущие комнаты — только стены (туман).
        - Текущая комната и видимые клетки — полное отображение.
        - Коридоры: пол отображаются только в поле зрения.

        Args:
            session (GameSessoion): Сессия (читаются ``level`` и ``hero_pos``).
            visible (set[tuple[int, int]]): Множество видимых клеток ``(x, y)``.
            current_room (Room): Комната, в которой стоит герой (или ``None``).
        """
        level = session.level
        for y in range(level.height):
            for x in range(level.width):
                ch, color = self._tile_char(
                    level, x, y, visible)
                self.safe_addstr(y, x, ch, color)

    def _tile_char(self, level: Level, x: int, y: int,
                   visible: set[tuple[int, int]]) -> tuple[str, int]:
        """Вычисляет символ и цвет тайла с учётом тумана войны.

        Args:
            level (Level): Текущий уровень.
            x, y (int): Координаты тайла.
            visible (set[tuple[int, int]]): Множество видимых клеток.

        Returns:
            tuple[str, int]: Кортеж ``(символ, атрибут_цвета_curses)``.
        """
        pos = Pos(x, y)
        room = level.room_for(pos)
        tile = level.tile_at(pos)
        is_visible = (x, y) in visible

        ch = " "

        if room is not None:
            if not room.discovered:
                ch = " "
            elif not is_visible:
                if x in (room.x1, room.x2) or y in (room.y1, room.y2):
                    ch = tile if tile != Tile.FLOOR.value else " "
                else:
                    ch = " "
            else:
                ch = tile
        else:
            if is_visible:
                ch = tile
            elif (x, y) in level.discovered_corridors:
                ch = tile if tile != Tile.FLOOR.value else " "

        # Цвет по типу тайла
        if ch == Tile.WALL.value:
            color = curses.color_pair(6)
        elif ch == Tile.EXIT.value:
            color = curses.color_pair(5)
        elif ch == Tile.FLOOR.value:
            color = curses.color_pair(4)
        elif ch == Tile.DOOR_RED.value:
            color = curses.color_pair(1) | curses.A_BOLD
        elif ch == Tile.DOOR_BLUE.value:
            color = curses.color_pair(6) | curses.A_BOLD
        elif ch == Tile.DOOR_YELLOW.value:
            color = curses.color_pair(3) | curses.A_BOLD
        else:
            color = 0

        return ch, color

    def _render_items(self, level: Level, visible: set[tuple[int, int]]) -> None:
        """Отрисовывает предметы на полу, если они видимы.

        Предметы в неизведанных комнатах и вне поля зрения не рисуются.

        Args:
            level (Level): Текущий уровень.
            visible (set[tuple[int, int]]): Множество видимых клеток.
        """
        for (x, y), item in level.items.items():
            if (x, y) not in visible:
                continue
            room = level.room_for(Pos(x, y))
            if room is not None and not room.discovered:
                continue
            sym, attr = self._item_symbol(item)
            self.safe_addstr(y, x, sym, attr)

    def _render_enemies(self, level: Level, visible: set[tuple[int, int]]) -> None:
        """Отрисовывает видимых врагов текущего уровня.

        Args:
            level (Level): Текущий уровень.
            visible (set[tuple[int, int]]): Множество видимых клеток.
        """
        for enemy in level.enemies.values():
            if (enemy.pos.x, enemy.pos.y) not in visible:
                continue
            if enemy.enemy_type == EnemyType.GHOST and not enemy.ghost_visible:
                continue

            symbol = enemy.symbol
            color = curses.color_pair(enemy.color)

            if enemy.enemy_type == EnemyType.MIMIC and enemy.disguised:
                symbol = enemy.mimic_item_symbol

            self.safe_addstr(
                enemy.pos.y,
                enemy.pos.x,
                symbol,
                color,
            )

    def _render_hero(self, session: GameSession) -> None:
        """Отрисовывает символ героя поверх всех остальных объектов.

        Герой всегда отображается жирным белым «@».

        Args:
            session (GameSession): Текущая игровая сессия.
        """
        self.safe_addstr(
            session.hero_pos.y,
            session.hero_pos.x,
            "@",
            curses.color_pair(5) | curses.A_BOLD,
        )

    def _render_hud(
        self,
        session: GameSession,
        mode_label: str = "2D",
        facing: int = 1,
    ) -> None:
        """Отрисовывает HUD: строки статуса, инвентаря, управления и сообщения.

        HUD располагается ниже карты. При недостаточной высоте терминала
        строки могут быть частично срезаны — ``safe_addstr`` игнорирует
        запросы вне видимой области.

        Строки HUD:
            +0: Уровень, HP, ловкость, сила, сокровища.
            +1: Текущее оружие, счётчики предметов в рюкзаке.
            +2: Подсказка по управлению.
            +3: Последнее сообщение из ``session.message``.

        Args:
            session (GameSession): Текущая игровая сессия.
        """
        level = session.level
        hero = session.hero

        if mode_label == "3D":
            # В 3D всегда прижимаем HUD к низу терминала, чтобы не перекрывать сцену.
            info_y = max(1, curses.LINES - 7)
        else:
            info_y = min(level.height + _HUD_OFFSET, curses.LINES - 7)

        # Характеристики героя
        hp_line = (
            f"[{mode_label}] Lvl {session.current_level}/21 | "
            f"HP {hero.hp}/{hero.max_hp} | "
            f"AGI {hero.agility} | STR {hero.strength} | "
            f"Treasure {session.backpack.treasure}"
        )

        # Оружие и инвентарь
        if hero.weapon:
            weapon_str = f"{hero.weapon.name} (+{hero.weapon.value})"
        else:
            weapon_str = "none"

        inv_line = (
            f"Weapon: {weapon_str} | "
            f"F:{len(session.backpack.food)} "
            f"E:{len(session.backpack.elixir)} "
            f"S:{len(session.backpack.scroll)} "
            f"W:{len(session.backpack.weapon)}"
        )

        keys_str = ", ".join(
            session.backpack.keys) if session.backpack.keys else "none"
        keys_line = f"Keys: {keys_str}"

        if mode_label == "3D":
            controls = "3D: W/S move | A/D turn | h/j/k/e items | Tab mode | q save | Esc quit"
            face = ["N", "E", "S", "W"][facing % 4]
            mode_line = f"Facing: {face}"
        else:
            controls = "2D: WASD move | h weapon | j food | k elixir | e scroll | q save | Esc quit"
            mode_line = ""

        # Сообщение
        message = session.message

        self.safe_addstr(info_y,     0, hp_line)
        self.safe_addstr(info_y + 1, 0, inv_line)
        self.safe_addstr(info_y + 2, 0, keys_line)
        self.safe_addstr(info_y + 3, 0, controls)
        self.safe_addstr(info_y + 4, 0, mode_line)

        display_msg = session.flash_message if session.flash_message else session.message
        attr = curses.color_pair(3) | curses.A_BOLD if session.flash_message else 0  # жёлтый жирный для flash
        self.safe_addstr(info_y + 5, 0, display_msg, attr)
        session.flash_message = ""



        # Если герой усыплён — предупреждение
        if session.sleep_turns > 0:
            self.safe_addstr(
                info_y + 6, 0,
                f"*** SLEEPING ({session.sleep_turns} turns) ***",
                curses.color_pair(1) | curses.A_BOLD,
            )

    def show_records(self, rows: list[RunStats]) -> None:
        """Отображает таблицу рекордов и ждёт нажатия клавиши.

        Args:
            rows (list[RunStats]): Список ``RunStats``, отсортированный по treasure (desc).
        """
        self.stdscr.erase()
        self.safe_addstr(1, 2, "Leaderboard (by treasure)", curses.A_BOLD)
        header = "#   Trea  Lvl  Kills  Food  Elix  Scroll  Hits+  Hits-  Steps"
        self.safe_addstr(3, 2, header)

        max_rows = max(0, curses.LINES - 7)
        for i, row in enumerate(rows[:max_rows], start=1):
            line = (
                f"{i:>2}  "
                f"{row.treasure:>6}  "
                f"{row.reached_level:>3}  "
                f"{row.kills:>5}  "
                f"{row.food_used:>4}  "
                f"{row.elixirs_used:>4}  "
                f"{row.scrolls_used:>6}  "
                f"{row.hits_dealt:>5}  "
                f"{row.hits_taken:>5}  "
                f"{row.steps:>5}"
            )
            self.safe_addstr(4 + i, 2, line)

        self.safe_addstr(curses.LINES - 2, 2, "Press any key to return")
        self.stdscr.refresh()
        self.stdscr.getch()

    def show_message(self, text: str) -> None:
        """Отображает одиночное сообщение на чистом экране и ждёт клавиши.

        Используется для уведомлений: «Game saved», «You died», «Victory!».

        Args:
            text (str): Текст сообщения.
        """
        self.stdscr.erase()
        self.safe_addstr(2, 2, text)
        self.stdscr.refresh()
        self.stdscr.getch()

    def _item_symbol(self, item: Item | ItemType) -> tuple[str, int]:
        """Возвращает символ и атрибут цвета для предмета."""
        if isinstance(item, ItemType):
            item_type = item
            item_name = ""
        else:
            item_type = item.item_type
            item_name = item.name

        if item_type == ItemType.TREASURE:
            return "$", curses.color_pair(3)
        if item_type == ItemType.FOOD:
            return "%", curses.color_pair(2)
        if item_type == ItemType.ELIXIR:
            return "!", curses.color_pair(5)
        if item_type == ItemType.SCROLL:
            return "?", curses.color_pair(7)
        if item_type == ItemType.KEY:
            color = curses.color_pair(5)
            if item_name == "red":
                color = curses.color_pair(1)
            elif item_name == "blue":
                color = curses.color_pair(6)
            elif item_name == "yellow":
                color = curses.color_pair(3)
            return "k", color | curses.A_BOLD
        return "/", curses.color_pair(1)

    def safe_addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        """Выводит строку.

        curses поднимает ``curses.error`` при записи в последнюю клетку
        экрана (правый нижний угол) — это стандартное поведение, которое
        нужно подавлять. Строка автоматически обрезается до ширины экрана.

        Args:
            y (int): Строка экрана (0 — верхняя).
            x (int): Столбец экрана (0 — левый).
            text (str): Выводимая строка.
            attr (int): Атрибуты curses (цвет, жирность и т.д.). По умолчанию 0.
        """
        if y < 0 or x < 0 or y >= curses.LINES or x >= curses.COLS:
            return
        max_len = max(0, curses.COLS - x)
        try:
            self.stdscr.addstr(y, x, text[:max_len], attr)
        except curses.error:
            pass

    def _init_colors(self) -> None:
        """Инициализирует цветовые пары curses.

        Вызывается один раз при создании ``Renderer``. Если терминал
        не поддерживает цвета — пропускается.

        Таблица пар:
            1=RED  2=GREEN  3=YELLOW  4=CYAN  5=WHITE  6=BLUE  7=MAGENTA
        """
        if not curses.has_colors():
            return
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_RED,     -1)
        curses.init_pair(2, curses.COLOR_GREEN,   -1)
        curses.init_pair(3, curses.COLOR_YELLOW,  -1)
        curses.init_pair(4, curses.COLOR_CYAN,    -1)
        curses.init_pair(5, curses.COLOR_WHITE,   -1)
        curses.init_pair(6, curses.COLOR_BLUE,    -1)
        curses.init_pair(7, curses.COLOR_MAGENTA, -1)

    def _check_terminal_size(self) -> bool:
        """Проверяет минимальный размер терминала и выводит предупреждение.

        Если терминал меньше ``_MIN_LINES * _MIN_COLS`` — рендеринг
        пропускается во избежание ошибок curses.

        Returns:
            bool: ``True`` если размер достаточен, ``False`` иначе.
        """
        if curses.LINES >= _MIN_LINES and curses.COLS >= _MIN_COLS:
            return True
        self.stdscr.erase()
        msg = f"Terminal too small: need {_MIN_COLS}x{_MIN_LINES}, got {curses.COLS}x{curses.LINES}"
        try:
            self.stdscr.addstr(0, 0, msg[:curses.COLS])
        except curses.error:
            pass
        self.stdscr.refresh()
        return False
