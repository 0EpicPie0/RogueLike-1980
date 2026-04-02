"""Боевая система игры.

Модуль отвечает за расчёт боя: формулы попадания и урона, атаки игрока и врагов, дроп сокровищ.
"""

from __future__ import annotations

import random

from domain.models import Enemy, EnemyType, GameSession, Item, ItemType, Level, Pos


def enemy_at(level: Level, pos: Pos) -> Enemy | None:
    """Возвращает значения живого видимого врага на клетке pos или None.

    Невидымые призраки не считаются целью.

    Args:
        level (Level): Текущий уровень с коллекцией врагов.
        pos (Pos):  Клетка, на которую наступает (или атакует) игрок.

    Returns:
        Enemy | None: Первый найденный живой враг, либо None.
    """
    for enemy in level.enemies.values():
        if enemy.pos == pos:
            if enemy.enemy_type == EnemyType.GHOST and not enemy.ghost_visible:
                continue
            if enemy.enemy_type == EnemyType.MIMIC and enemy.disguised:
                return enemy

            return enemy
    return None


def attack_hits(rng: random.Random, attacker_agility: int, target_agility: int) -> bool:
    """Определяет, попадает ли удар, по ловкости атакующего и цели. 

    Вероятность попадания линейно зависит от разницы ловкостей и зажата 
    в диапазоне [10%, 90%], чтобы исключить гарантированные промахи/попадания.
    Формула:
        chance = 0.55 + (attacker_agility - target_agility) * 0.03
        chance = clamp(chance, 0.10, 0.90)

    Args:
        rng (random.Random): Генератор случайных чисел.
        attacker_agility (int): Ловкость атакующего.
        target_agility (int): Ловкость цели удара.

    Returns:
        bool: True если удар попадает, иначе False.
    """
    chance = 0.55 + (attacker_agility - target_agility) * 0.03
    chance = max(0.10, min(0.90, chance))
    return rng.random() < chance


def compute_damage(rng: random.Random, strength: int, weapon_bonus: int) -> int:
    """Рассчитывает итоговый урон от одного удара. 

    Урон складывается из трех компонентов: 
    - базовая сила атакующего, 
    - случайный разброс, 
    - случайный бонус оружия [0, weapon_bonus]; 0 если без оружия. 

    Минимальный урон - 1.

    Args:
        rng (random.Random): Генератор случайных чисел.
        strength (int): Сила атакующего.
        weapon_bonus (int): Значение силы снаряженного оружия (0 если без оружия).

    Returns:
        int: Итоговый урон (>= 1).
    """
    variance = rng.randint(0, max(1, strength // 2 + 1))
    weapon_roll = rng.randint(0, max(0, weapon_bonus))
    return max(1, strength + variance + weapon_roll)


def treasure_drop(rng: random.Random, enemy: Enemy) -> int:
    """Рассчитывает количество сокровищ, выпадающих с убитого врага. 

    Награда пропорциональной весу противника: враждебности, ловкости, 
    силе и максимальному здоровью. Сильные и живые враги ценятся больше.

    Args:
        rng (random.Random): Генератор случайных чисел
        enemy (Enemy): Убитый противник.

    Returns:
        int: Количество сокровищ (>= 1).
    """
    base = enemy.hostility + enemy.agility // 2 + enemy.strength + enemy.max_hp // 5
    return max(1, rng.randint(base // 3, base // 2 + 1))


def player_attack(rng: random.Random, session: GameSession, enemy: Enemy) -> str:
    """Рассчитывает удар игры по врагу. 

    Args:
        rng (random.Random): Генератор случайных чисел.
        session (GameSession): Текущая игровая сессия. Изменяет level.enemies, backpack, stats.
        enemy (Enemy): Враг, по которому наносится удар.

    Returns:
        str: Сообщение для отображения игроку.
    """
    hero = session.hero

    if enemy.enemy_type == EnemyType.VAMPIRE and enemy.vampire_first_hit_block:
        enemy.vampire_first_hit_block = False
        return "First strike misses the vampire's mist form."

    if not attack_hits(rng, hero.agility, enemy.agility):
        return f"You miss {enemy.enemy_type.value}."

    weapon_bonus = hero.weapon.value if hero.weapon else 0
    damage = compute_damage(rng, hero.strength, weapon_bonus)

    enemy.hp -= damage
    session.stats.hits_dealt += 1

    if enemy.hp > 0:
        return f"You hit {enemy.enemy_type.value} for {damage}."

    gained = treasure_drop(rng, enemy)
    session.level.enemies.pop(enemy.enemy_id, None)
    session.stats.kills += 1

    session.backpack.add_item(
        Item(item_type=ItemType.TREASURE, name="treasure", value=gained)
    )
    session.stats.treasure = session.backpack.treasure

    return (
        f"You hit {enemy.enemy_type.value} for {damage} and kill it. "
        f"+{gained} treasure."
    )


def enemy_attack(rng: random.Random, session: GameSession, enemy: Enemy) -> None:
    """Рассчитывает удар врага по игре и обновляет состояние сессии. 

    Специальные эффекты: 
    - Вампир: удар уменьшает HP героя на 1-2. 
    - Змей-маг: 30% шанс усыпить героя на 1 ход. 
    - Огр: после удара уходит в отдых на 1 ход, затем следующий удар - гарантированный. 

    Args:
        rng (random.Random): Генератор случайных чисел.
        session (GameSession): Текущая игровая сессия. 
        enemy (Enemy): Враг, наносящий удар.
    """
    hero = session.hero

    guaranteed = enemy.enemy_type == EnemyType.OGRE and enemy.ogre_guaranteed_hit
    if not guaranteed and not attack_hits(rng, enemy.agility, hero.agility):
        return

    damage = compute_damage(rng, enemy.strength, weapon_bonus=0)
    hero.hp -= damage
    session.stats.hits_taken += 1
    session.level_damage_taken += damage  # Метрика за уровень

    if enemy.enemy_type == EnemyType.GHOST:
        enemy.ghost_engaged = True
    if enemy.enemy_type == EnemyType.VAMPIRE:
        _apply_vampire_drain(rng, session)
    elif enemy.enemy_type == EnemyType.SNAKE_MAGE:
        _apply_snake_sleep(rng, session)
    elif enemy.enemy_type == EnemyType.OGRE:
        _apply_ogre_rest(enemy)

    if hero.hp <= 0:
        session.game_over = True
        session.victory = False
        session.message = "You died."


def _apply_vampire_drain(rng: random.Random, session: GameSession) -> None:
    """Вампир уменьшает HP героя на 1-2 после успешной атаки. 

    HP не опускается ниже 1. Если hp превышает новый max_hp, оно обрезается сверху.

    Args:
        rng (random.Random): Генератор случайных чисел.
        session (GameSession): Текущая игровая сессия. 
    """
    drain = rng.randint(1, 2)
    session.hero.max_hp = max(1, session.hero.max_hp - drain)
    session.hero.hp = min(session.hero.hp, session.hero.max_hp)


def _apply_snake_sleep(rng: random.Random, session: GameSession) -> None:
    """С вероятностью 30% усыпляет героя на 1 ход после атаки Змея-мага. 
    Длительность не суммируется - берется максимум из текущего и нового значения.

    Args:
        rng (random.Random): Генератор случайных чисел.
        session (GameSession): Текущая игровая сессия. 
    """
    if rng.random() < 0.30:
        session.sleep_turns = max(session.sleep_turns, 1)


def _apply_ogre_rest(enemy: Enemy) -> None:
    """Переводит Огра в режиме отдыха на 1 ход после атаки. 

    После отдыха следующий удар Огра будет гарантированным. 
    Флаг сбрасывается - он уже был использован в текущем ударе.

    Args:
        enemy (Enemy): Враг.
    """
    enemy.ogre_rest_turns = 1
    enemy.ogre_guaranteed_hit = False


def manhattan_distance(a: Pos, b: Pos) -> int:
    """Возвращает манхэттенское расстояние между двумя позициями.

    Используется для определения дальности, с которой враг начинает
    преследование.

    Args:
        a (Pos): Первая позиция.
        b (Pos): Вторая позиция.

    Returns:
        int: Дальность.
    """
    return abs(a.x - b.x) + abs(a.y - b.y)


def is_adjacent(a: Pos, b: Pos, *, allow_diagonal: bool) -> bool:
    """Проверяет, являются ли две клетки соседними.

    Args:
        a (Pos): Первая позиция.
        b (Pos): Вторая позиция.
        allow_diagonal (bool): Если True — диагональные клетки тоже считаются соседними.
                               Если False — только 4 кардинальных направления.

    Returns:
        bool: True, если клетки соседние.
    """
    dx = abs(a.x - b.x)
    dy = abs(a.y - b.y)
    if allow_diagonal:
        return max(dx, dy) == 1
    return dx + dy == 1
