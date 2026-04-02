"""ИИ и движение противников.

Модуль управляет поведением врагов за один ход: обновлением видимости
призрака, отдыхом огра, преследованием игрока и патрульными паттернами.
"""

from __future__ import annotations

import random
from collections import deque

from domain.combat import enemy_attack, is_adjacent, manhattan_distance
from domain.models import Enemy, EnemyType, GameSession, Level, Pos


CARDINAL_DIRS: list[tuple[int, int]] = [(1, 0), (-1, 0), (0, 1), (0, -1)]


def enemy_turn(rng: random.Random, session: GameSession) -> None:
    """Выполняет ход всех живых врагов текущего уровня.

    Вызывается ``GameEngine`` после каждого действия игрока. Если сессия
    уже завершена (``session.game_over``), функция возвращается немедленно.

    Args:
        rng (random.Random): Генератор случайных чисел (передаётся от GameEngine).
        session (GameSession): Текущая игровая сессия. Изменяет позиции врагов,
                               hero.hp, game_over и связанные поля.
    """
    if session.game_over:
        return

    # Копия values() — враги могут погибнуть в ходе итерации (от _resolve_kill),
    # итерация по живому словарю привела бы к RuntimeError.
    for enemy in list(session.level.enemies.values()):
        if not enemy.alive():
            continue

        if enemy.enemy_type == EnemyType.MIMIC and enemy.disguised:
            continue

        _process_enemy(rng, session, enemy)

        if session.game_over:
            return


def _process_enemy(rng: random.Random, session: GameSession, enemy: Enemy) -> None:
    """Выполняет ход одного врага: спецсостояние - атака или движение.

    Args:
        rng (random.Random): Генератор случайных чисел.
        session (GameSession): Текущая игровая сессия.
        enemy (Enemy): Враг, чей ход обрабатывается.
    """
    hero_pos = session.hero_pos
    level = session.level

    _update_special_state(rng, enemy, hero_pos)

    if enemy.enemy_type == EnemyType.OGRE and enemy.ogre_rest_turns > 0:
        enemy.ogre_rest_turns -= 1
        if enemy.ogre_rest_turns == 0:
            enemy.ogre_guaranteed_hit = True
        return

    if is_adjacent(enemy.pos, hero_pos, allow_diagonal=True):
        enemy_attack(rng, session, enemy)
        return

    if manhattan_distance(enemy.pos, hero_pos) <= enemy.hostility:
        path = shortest_path(level, enemy.pos, hero_pos)
        if path and len(path) > 1:
            next_step = path[1]
            if next_step == hero_pos:
                enemy_attack(rng, session, enemy)
            elif can_move_to(level, enemy, next_step):
                enemy.pos = next_step
            return

    _patrol(rng, level, enemy)


def _update_special_state(rng: random.Random, enemy: Enemy, hero_pos: Pos) -> None:
    """Обновляет специфичные для типа флаги врага до начала его хода.

    Вызывается первым в :func:`_process_enemy`. Каждый тип врага имеет
    своё состояние, обновляемое каждый ход независимо от того, атакует
    враг или патрулирует.

    Args:
        rng (random.Random): Генератор случайных чисел (для случайности видимости Призраков).
        enemy (Enemy): Враг, чьё состояние обновляется.
        hero_pos (Pos): Позиция героя на момент хода.
    """
    if enemy.enemy_type == EnemyType.GHOST:
        _update_ghost_visibility(rng, enemy, hero_pos)


def _update_ghost_visibility(rng: random.Random, enemy: Enemy, hero_pos: Pos) -> None:
    """Обновляет видимость Призрака.

    Правило: Ghost периодически становится невидимым *пока игрок
    не вступил в бой*. После первого боевого контакта Ghost остаётся
    видимым постоянно.

    Признаки вступления в бой (поле Enemy.ghost_engaged):
    - Игрок ударил Ghost (устанавливается в GameEngine.move_player).
    - Ghost ударил игрока (устанавливается в enemy_attack при Ghost).

    Args:
        rng (random.Random): Генератор случайных чисел.
        enemy (Enemy): Призрак, чья видимость обновляется.
        hero_pos (Pos): Позиция героя.
    """
    if enemy.ghost_engaged:
        enemy.ghost_visible = True
        return

    in_range = manhattan_distance(enemy.pos, hero_pos) <= enemy.hostility
    adjacent = is_adjacent(enemy.pos, hero_pos, allow_diagonal=True)

    if in_range or adjacent:
        enemy.ghost_visible = True
    else:
        enemy.ghost_visible = rng.random() > 0.35


def _patrol(rng: random.Random, level: Level, enemy: Enemy) -> None:
    """Диспетчер патрульных паттернов: вызывает нужную функцию по типу врага.

    Каждый тип врага имеет уникальный паттерн движения.

    Args:
        rng (random.Random): Генератор случайных чисел.
        level (Level): Уровень, по которому перемещается враг.
        enemy (Enemy): Враг, совершающий патрульный ход.
    """
    match enemy.enemy_type:
        case EnemyType.GHOST:
            _patrol_ghost(rng, level, enemy)
        case EnemyType.OGRE:
            _patrol_ogre(rng, level, enemy)
        case EnemyType.SNAKE_MAGE:
            _patrol_snake(level, enemy)
        case _:
            # Зомби и Вампир: случайное кардинальное движение
            _patrol_random_cardinal(rng, level, enemy)


def _patrol_ghost(rng: random.Random, level: Level, enemy: Enemy) -> None:
    """Паттерн Призрак: телепортация в случайную клетку текущей комнаты.

    Призрак «постоянно телепортируется по комнате». Если Призрак вышел в коридор ход пропускается.

    Args:
        rng (random.Random): Генератор случайных чисел.
        level (Level): Текущий уровень.
        enemy (Enemy): Призрак, совершающий телепорт.
    """
    room = level.room_for(enemy.pos)
    if room is None:
        return
    cells = room.random_floor_cells()
    if cells:
        enemy.pos = rng.choice(cells)


def _patrol_ogre(rng: random.Random, level: Level, enemy: Enemy) -> None:
    """Паттерн Огр: ходит на две клетки в кардинальном направлении.

    Огр «ходит по комнате на две клетки». Направление перебирается
    случайно до нахождения свободного двойного шага. Обе промежуточные
    клетки проверяются на проходимость: p1 и p2 должны быть свободны.

    Args:
        rng (random.Random): Генератор случайных чисел.
        level (Level): Текущий уровень.
        enemy (Enemy): Огр, соверщающий ход.
    """
    dirs = CARDINAL_DIRS[:]
    rng.shuffle(dirs)
    for dx, dy in dirs:
        p1 = enemy.pos + (dx, dy)
        p2 = enemy.pos + (2 * dx, 2 * dy)
        if can_move_to(level, enemy, p1) and can_move_to(level, enemy, p2):
            enemy.pos = p2
            return


def _patrol_snake(level: Level, enemy: Enemy) -> None:
    """Паттерн Змей-маг: ходит по диагонали, отражается от стен.

    Змей-маг «ходит по карте по диагонали, постоянно меняя сторону».
    Если диагональный шаг заблокирован — направление инвертируется и
    делается попытка в обратную сторону. Если и там нет хода — стоит на месте.

    Args:
        level (Level): Текущий уровень.
        enemy (Enemy): Змей-маг, совершающий ход.
    """
    dx, dy = enemy.snake_diag_dir
    target = enemy.pos + (dx, dy)
    if can_move_to(level, enemy, target):
        enemy.pos = target
        return
    # Отражение: инвертируем направление
    enemy.snake_diag_dir = (-dx, -dy)
    target = enemy.pos + enemy.snake_diag_dir
    if can_move_to(level, enemy, target):
        enemy.pos = target


def _patrol_random_cardinal(rng: random.Random, level: Level, enemy: Enemy) -> None:
    """Паттерн по умолчанию (Зомби, Вампир): случайный шаг в кардинальном направлении.

    Перебирает все 4 направления в случайном порядке и делает первый
    допустимый шаг. Если все направления заблокированы — стоит на месте.

    Args:
        rng (random.Random): Генератор случайных чисел.
        level (Level): Текущий уровень.
        enemy (Enemy): Враг, соверщающий ход.
    """
    dirs = CARDINAL_DIRS[:]
    rng.shuffle(dirs)
    for dx, dy in dirs:
        target = enemy.pos + (dx, dy)
        if can_move_to(level, enemy, target):
            enemy.pos = target
            return


def can_move_to(level: Level, enemy: Enemy, pos: Pos) -> bool:
    """Проверяет, может ли враг переместиться на клетку ``pos``.

    Клетка доступна, если:
    - она находится внутри уровня и проходима (не стена),
    - на ней не стоит другой живой враг.

    Герой не блокирует движение — враг «заходит» на его клетку
    как атаку (обрабатывается отдельно в :func:`_process_enemy`).

    Args:
        level (Level): Текущий уровень.
        enemy (Enemy): Враг, для которого проверяется доступность.
        pos (Pos): Целевая клетка.

    Returns:
        ``True``, если перемещение допустимо, иначе ``False``.
    """
    if not level.is_walkable(pos):
        return False
    return not any(
        other.enemy_id != enemy.enemy_id and other.pos == pos
        for other in level.enemies.values()
    )


def shortest_path(level: Level, start: Pos, goal: Pos) -> list[Pos] | None:
    """Находит кратчайший путь между двумя клетками методом BFS.

    Используется при преследовании игрока.

    Если пути не существует (игрок отрезан стенами), возвращает ``None``.
    В этом случае ``_process_enemy`` переключает врага на патруль.

    Args:
        level (Level): Уровень, по которому строится путь.
        start (Pos): Стартовая позиция (позиция врага).
        goal (Pos): Целевая позиция (позиция героя).

    Returns:
        list[Pos] | None: Список позиций от ``start`` до ``goal`` включительно, или ``None``
                          если путь не найден. Минимальная длина пути — 2 элемента
                          (``[start, goal]`` для соседних клеток).
    """
    queue: deque[Pos] = deque([start])
    came_from: dict[tuple[int, int], tuple[int, int] | None] = {
        (start.x, start.y): None
    }

    while queue:
        current = queue.popleft()
        if current == goal:
            break
        for dx, dy in CARDINAL_DIRS:
            nxt = current + (dx, dy)
            key = (nxt.x, nxt.y)
            if key in came_from:
                continue
            if not level.is_walkable(nxt) and nxt != goal:
                continue
            came_from[key] = (current.x, current.y)
            queue.append(nxt)

    if (goal.x, goal.y) not in came_from:
        return None

    path: list[Pos] = []
    cur: tuple[int, int] | None = (goal.x, goal.y)
    while cur is not None:
        path.append(Pos(cur[0], cur[1]))
        cur = came_from[cur]
    path.reverse()
    return path
