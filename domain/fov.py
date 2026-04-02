"""Field-of-View (FOV) и обнаружение уровня.

Модуль реализует:
- Алгоритм Брезенхэма для построения прямых линий на сетке.
- Ray Casting — вычисление множества видимых клеток из позиции героя.
- Обновление состояния «тумана войны» (какие комнаты и коридоры открыты).

Типичный порядок вызова за один ход:
    visible = compute_visible_cells(session)
    update_discovery(session, visible)
"""

from __future__ import annotations

from domain.models import GameSession, Level, Pos, Tile


def bresenham_line(a: Pos, b: Pos) -> list[Pos]:
    """Возвращает список клеток на прямой между ``a`` и ``b`` (включительно).

    Использует целочисленный алгоритм Брезенхэма без деления с плавающей
    точкой, что обеспечивает детерминированный и воспроизводимый результат.

    Args:
        a (Pos): Начальная точка.
        b (Pos): Конечная точка.

    Returns:
        list[Pos]: Упорядоченный список клеток от ``a`` до ``b``.
                   Всегда содержит как минимум одну точку (саму ``a``).

    Example:
        >>> bresenham_line(Pos(0, 0), Pos(3, 1))
        [Pos(x=0, y=0), Pos(x=1, y=0), Pos(x=2, y=1), Pos(x=3, y=1)]
    """
    points: list[Pos] = []
    x0, y0 = a.x, a.y
    x1, y1 = b.x, b.y

    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy

    while True:
        points.append(Pos(x0, y0))
        if x0 == x1 and y0 == y1:
            return points
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def compute_visible_cells(session: GameSession, radius: int = 12) -> set[tuple[int, int]]:
    """Вычисляет множество клеток, видимых герою, методом Ray Casting.

    Алгоритм: из позиции героя пускаются лучи (Брезенхэм) к каждой клетке
    на периметре квадрата ``radius * radius``. Луч добавляет клетки в
    множество видимых до тех пор, пока не упирается в стену или край карты.

    Args:
        session (GameSession): Текущая игровая сессия. Читает ``hero_pos`` и ``level``.
        radius (int, optional): Радиус обзора в клетках (по умолчанию 12). Определяет размер
                                периметра, к которому пускаются лучи.

    Returns:
        set[tuple[int, int]]: Множество координат ``(x, y)`` видимых клеток. Всегда включает
                              клетку самого героя.

    Note:
        Функция не изменяет состояние сессии — только читает его.
        Для обновления тумана войны используй :func:`update_discovery`.
    """
    # r = radius if radius is not None else 12  # Радиус поля зрения героя
    origin = session.hero_pos
    level = session.level
    visible: set[tuple[int, int]] = {(origin.x, origin.y)}

    # Верхний и нижний края периметра
    for edge_x in range(origin.x - radius, origin.x + radius + 1):
        for edge_y in (origin.y - radius, origin.y + radius):
            _cast_ray(level, origin, Pos(edge_x, edge_y), visible)

    # Левый и правый края периметра (без угловых, они уже обработаны выше)
    for edge_y in range(origin.y - radius + 1, origin.y + radius):
        for edge_x in (origin.x - radius, origin.x + radius):
            _cast_ray(level, origin, Pos(edge_x, edge_y), visible)

    return visible


def _cast_ray(level: Level, src: Pos, dst: Pos, visible: set[tuple[int, int]]) -> None:
    """Пускает одиночный луч от ``src`` к ``dst``, добавляя клетки в ``visible``.

    Args:
        level (Level): Уровень, на котором строится луч.
        src (Pos): Источник луча (позиция героя).
        dst (Pos): Цель луча (точка на периметре).
        visible (set[tuple[int, int]]): Мутируемое множество, в которое добавляются видимые клетки.
    """
    for point in bresenham_line(src, dst):
        if not level.is_inside(point):
            return
        visible.add((point.x, point.y))
        if point != src and level.tile_at(point) in {
            Tile.WALL.value,
            Tile.DOOR_RED.value,
            Tile.DOOR_BLUE.value,
            Tile.DOOR_YELLOW.value,
        }:
            return


def update_discovery(session: GameSession, visible: set[tuple[int, int]] | None = None) -> None:
    """Обновляет флаги «открытости» комнат и коридоров на основе видимых клеток.

    Логика тумана войны:
    - Комната, в которой стоит герой, помечается как ``discovered``.
    - Любая комната, хотя бы одна клетка которой попала в ``visible``,
      тоже помечается как ``discovered`` (видна частично из коридора).
    - Клетки коридоров вне комнат добавляются в ``level.discovered_corridors``.

    Args:
        session (GameSession): Текущая игровая сессия. Изменяет ``room.discovered`` и
                               ``level.discovered_corridors``.
        visible (set[tuple[int, int]] | None, optional): Предвычисленное множество видимых клеток. 
        Если не передано, вычисляется через :func:`compute_visible_cells`. Передавай явно, если в 
        этом же ходу уже вызывал ``compute_visible_cells``, чтобы избежать двойного трейсинга лучей.

    Example:
        # Эффективный вариант — одно вычисление видимости за ход:
        >>> visible = compute_visible_cells(session)
        update_discovery(session, visible)
        render(session, visible)
    """
    if visible is None:
        visible = compute_visible_cells(session)

    level = session.level

    current_room = level.room_for(session.hero_pos)
    if current_room is not None:
        current_room.discovered = True

    for room in level.rooms:
        if room.discovered:
            continue
        for y in range(room.y1, room.y2 + 1):
            for x in range(room.x1, room.x2 + 1):
                if (x, y) in visible:
                    room.discovered = True
                    break
            if room.discovered:
                break

    for x, y in visible:
        if level.room_for(Pos(x, y)) is None:
            level.discovered_corridors.add((x, y))
