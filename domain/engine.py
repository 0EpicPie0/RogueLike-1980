"""Игровой движок: оркестратор игровой сессии.

Класс ``GameEngine`` — координирует вызовы специализированных
модулей и управляет состоянием сессии.


Жизненный цикл сессии:
    session = engine.new_session()          # старт новой игры
    # или
    session = engine.from_saved(raw)        # восстановление из сохранения

    while True:
        engine.move_player(session, dx, dy) # ход игрока
        # или
        engine.use_item(session, type, idx) # использование предмета

    data_layer.save_session(session)        # сохранение
"""

from __future__ import annotations

import random

from domain.ai import enemy_turn
from domain.combat import enemy_at, player_attack
from domain.fov import compute_visible_cells, update_discovery
from domain.generator import generate_level
from domain.models import (
    ActiveEffect,
    Backpack,
    Character,
    Enemy,
    EnemyType,
    GameSession,
    Item,
    ItemType,
    Pos,
    RunStats,
    StatType,
    Level,
    Tile,
)

DIAGONAL_DIRS: list[tuple[int, int]] = [(1, 1), (1, -1), (-1, 1), (-1, -1)]
CARDINAL_DIRS: list[tuple[int, int]] = [(1, 0), (-1, 0), (0, 1), (0, -1)]

# Начальные характеристики героя
_HERO_MAX_HP: int = 35
_HERO_AGILITY: int = 9
_HERO_STRENGTH: int = 6


class GameEngine:
    """Оркестратор игровой сессии.

    Attributes:
        rng: Генератор случайных чисел. Состояние сохраняется в
             ``GameSession.rng_state`` при вызове ``save_session``,
             что обеспечивает полное воспроизведение сессии после
             перезапуска.
    """

    def __init__(self, seed: int | None = None) -> None:
        """Инициализирует движок с опциональным фиксированным seed.

        Args:
            seed (int): Seed для ``random.Random``. Если ``None`` — случайный.
        """
        self.rng = random.Random(seed)

    def new_session(self) -> GameSession:
        """Создаёт новую игровую сессию с первым уровнем и начальным героем.

        Returns:
            GameSession: Полностью инициализированная сессия, готовая к первому ходу.
                         Туман войны уже обновлён для стартовой позиции.
        """
        level, start_pos = generate_level(1, self.rng)
        hero = Character(
            max_hp=_HERO_MAX_HP,
            hp=_HERO_MAX_HP,
            agility=_HERO_AGILITY,
            strength=_HERO_STRENGTH,
        )
        session = GameSession(
            hero=hero,
            hero_pos=start_pos,
            level=level,
            backpack=Backpack(),
            active_effects=[],
            stats=RunStats(),
            current_level=1,
            message="Find the exit of level 1.",
        )
        visible = compute_visible_cells(session)
        update_discovery(session, visible)
        session.last_visible = visible
        return session

    def from_saved(self, session: GameSession) -> GameSession:
        """Восстанавливает сессию из сохранения, включая состояние rng.

        Args:
            session (GameSession): Сессия, загруженная из ``JsonDataLayer.load_session``.

        Returns:
            GameSession: Та же сессия с обновлённым туманом войны.

        Note:
            Если ``session.rng_state`` равен ``None`` (старое сохранение
            без состояния rng), движок продолжает с текущим rng — сессия
            будет воспроизведена корректно, но случайность не совпадёт.
        """
        if session.rng_state is not None:
            self.rng.setstate(session.rng_state)
        visible = compute_visible_cells(session)
        update_discovery(session, visible)
        session.last_visible = visible
        return session

    def save_rng_state(self, session: GameSession) -> None:
        """Сохраняет текущее состояние rng в сессию для последующего восстановления.

        Должна вызываться перед ``JsonDataLayer.save_session``. ``curses_app`` вызывает 
        её автоматически при сохранении по клавише Q и при переходе на следующий уровень.

        Args:
            session (GameSession): Сессия, в которую сохраняется состояние rng.
        """
        session.rng_state = self.rng.getstate()

    def move_player(self, session: GameSession, dx: int, dy: int) -> str:
        """Обрабатывает ход игрока в направлении (dx, dy).

        Args:
            session (GameSession): Текущая игровая сессия (мутируется).
            dx (int): Смещение по горизонтали (-1, 0, 1).
            dy (int): Смещение по вертикали (-1, 0, 1).

        Returns:
            str: Строка-сообщение для отображения в статусной строке.
        """
        if session.game_over:
            return session.message

        # Ход пропущен из-за сна
        if session.sleep_turns > 0:
            session.sleep_turns -= 1
            self._end_turn(session)
            session.message = "You are asleep and miss this turn."
            return session.message

        target = session.hero_pos + (dx, dy)

        if not session.level.is_inside(target):
            session.message = "You bump into darkness."
            return session.message

        if not session.level.is_walkable(target):
            tile = session.level.tile_at(target)
            doors = {
                Tile.DOOR_RED.value: "red",
                Tile.DOOR_BLUE.value: "blue",
                Tile.DOOR_YELLOW.value: "yellow",
            }
            if tile in doors:
                color = doors[tile]
                if color in session.backpack.keys:
                    session.level.set_tile(target, Tile.FLOOR.value)
                    session.message = f"You unlocked the {color} door."
                    self._end_turn(session)
                    return session.message
                session.message = f"You need the {color} key to open this door."
                return session.message

            session.message = "A wall blocks your path."
            return session.message

        # Атака при наступании на врага
        enemy = enemy_at(session.level, target)
        if enemy is not None:
            reveal_msg = ""
            if enemy.enemy_type == EnemyType.MIMIC and enemy.disguised:
                enemy.disguised = False
                reveal_msg = "The item suddenly transforms into a Mimic! "

            attack_msg = self._handle_attack(session, enemy)

            if reveal_msg:
                session.flash_message = reveal_msg + attack_msg
            return attack_msg


        # Перемещение
        session.hero_pos = target
        session.stats.steps += 1
        msg_parts: list[str] = []

        # Подбор предмета
        pickup_msg = self._handle_pickup(session, target)
        if pickup_msg:
            msg_parts.append(pickup_msg)

        # Проверка выхода
        if target == session.level.exit_pos:
            msg_parts.append(self._advance_level(session))
        else:
            self._end_turn(session)

        session.message = " ".join(msg_parts) if msg_parts else "You move."
        return session.message

    def use_item(self, session: GameSession, item_type: ItemType, index: int) -> str:
        """Применяет предмет из рюкзака и завершает ход.

        Индексация предметов соответствует отображению в инвентаре:
        ``index=0`` означает «убрать оружие» (только для ``WEAPON``),
        ``index=1..9`` — применить предмет с этим номером в списке.

        Каждый тип предмета при использовании:
        - **FOOD**: восстанавливает hp до max_hp, тратится.
        - **ELIXIR**: временно повышает характеристику, тратится, создаёт ActiveEffect.
        - **SCROLL**: постоянно повышает характеристику, тратится.
        - **WEAPON**: снаряжается; старое оружие выбрасывается рядом или
                      возвращается в рюкзак, если нет свободной клетки.

        Args:
            session (GameSession): Текущая игровая сессия (мутируется).
            item_type (ItemType): Тип предмета из рюкзака.
            index (int): Номер предмета в списке (1–9) или 0 для снятия оружия.

        Returns:
            str: Строка-сообщение для отображения в статусной строке.
        """
        if session.game_over:
            return session.message

        # Снятие оружия (index=0 только для WEAPON)
        if item_type == ItemType.WEAPON and index == 0:
            return self._unequip_weapon(session)

        bucket = session.backpack.bucket(item_type)
        if index < 1 or index > len(bucket):
            session.message = "Invalid item index."
            return session.message

        item = bucket[index - 1]
        msg = ""

        if item_type == ItemType.FOOD:
            msg = self._use_food(session, bucket, index)

        elif item_type == ItemType.ELIXIR:
            msg = self._use_elixir(session, bucket, index, item)

        elif item_type == ItemType.SCROLL:
            msg = self._use_scroll(session, bucket, index, item)

        elif item_type == ItemType.WEAPON:
            msg = self._equip_weapon(session, bucket, index)

        if not session.game_over:
            self._end_turn(session)

        session.message = msg
        return msg

    def _unequip_weapon(self, session: GameSession) -> str:
        """Убирает текущее оружие в рюкзак без броска на пол.

        Args:
            session (GameSession): Текущая игровая сессия.

        Returns:
            str: Строка-сообщение.
        """
        if session.hero.weapon is None:
            session.message = "You are already unarmed."
            return session.message
        session.backpack.weapon.append(session.hero.weapon)
        session.hero.weapon = None
        self._end_turn(session)
        session.message = "You put weapon away."
        return session.message

    def _use_food(self, session: GameSession, bucket: list, index: int) -> str:
        """Применяет еду: восстанавливает HP, обновляет статистику.

        Args:
            session (GameSession): Текущая игровая сессия.
            bucket (list): Список предметов типа FOOD из рюкзака.
            index (int): 1-based индекс предмета в списке.

        Returns:
            str: Строка-сообщение с количеством восстановленного HP.
        """
        item = bucket.pop(index - 1)
        old_hp = session.hero.hp
        session.hero.hp = min(session.hero.max_hp,
                              session.hero.hp + item.value)
        healed = session.hero.hp - old_hp
        session.stats.food_used += 1
        return f"You eat {item.name} and heal {healed} HP."

    def _use_elixir(self, session: GameSession, bucket: list, index: int, item: Item) -> str:
        """Применяет эликсир: временно повышает характеристику.

        Создаёт ``ActiveEffect``, который будет снят через ``item.duration``
        ходов в ``_tick_effects``.

        Args:
            session (GameSession): Текущая игровая сессия.
            bucket (list): Список предметов типа ELIXIR из рюкзака.
            index (int): 1-based индекс предмета в списке.
            item (Item): Предмет (уже извлечён из bucket для чтения полей).

        Returns:
            str: Строка-сообщение с эффектом и длительностью.
        """
        bucket.pop(index - 1)
        self._apply_stat_delta(session, item.stat, item.value)
        session.active_effects.append(
            ActiveEffect(
                stat=item.stat or StatType.STRENGTH,
                value=item.value,
                turns_left=item.duration,
            )
        )
        session.stats.elixirs_used += 1
        return f"You drink {item.name}. +{item.value} {item.stat} for {item.duration} turns."

    def _use_scroll(self, session: GameSession, bucket: list, index: int, item: Item) -> str:
        """Применяет свиток: постоянно повышает характеристику.

        В отличие от эликсира не создаёт ``ActiveEffect`` — эффект необратим.

        Args:
            session (GameSession): Текущая игровая сессия.
            bucket (list): Список предметов типа SCROLL из рюкзака.
            index (int): 1-based индекс предмета в списке.
            item (Item): Предмет (уже извлечён из bucket для чтения полей).

        Returns:
            str: Строка-сообщение с постоянным изменением характеристики.
        """
        bucket.pop(index - 1)
        self._apply_stat_delta(session, item.stat, item.value)
        session.stats.scrolls_used += 1
        return f"You read {item.name}. Permanent +{item.value} {item.stat}."

    def _equip_weapon(self, session: GameSession, bucket: list, index: int) -> str:
        """Снаряжает оружие; старое выбрасывает рядом или возвращает в рюкзак.

        Оружие при смене должно падать на пол на соседнюю клетку.
        Если все соседние клетки заняты — оружие возвращается в рюкзак,
        а игрок получает соответствующее сообщение.

        Args:
            session (GameSession): Текущая игровая сессия.
            bucket (list): Список предметов типа WEAPON из рюкзака.
            index (int): 1-based индекс предмета в списке.

        Returns:
            str: Строка-сообщение о снаряжении оружия и судьбе старого.
        """
        chosen = bucket.pop(index - 1)
        old_weapon = session.hero.weapon
        session.hero.weapon = chosen
        msg = f"You equip {chosen.name} (+{chosen.value} power)."

        if old_weapon is not None:
            drop_pos = self._free_adjacent_pos(session.level, session.hero_pos)
            if drop_pos:
                session.level.items[(drop_pos.x, drop_pos.y)] = old_weapon
                msg += " Old weapon dropped nearby."
            else:
                session.backpack.weapon.append(old_weapon)
                msg += " No free cell nearby, old weapon returned to backpack."

        return msg

    def _handle_attack(self, session: GameSession, enemy: Enemy) -> str:
        """Обрабатывает атаку игрока по врагу на соседней клетке.

        Args:
            session (GameSession): Текущая игровая сессия.
            enemy (Enemy): Враг на целевой клетке.

        Returns:
            str: Строка-сообщение для статусной строки.
        """
        if enemy.enemy_type == EnemyType.GHOST:
            enemy.ghost_engaged = True
        msg = player_attack(self.rng, session, enemy)
        if not session.game_over:
            self._end_turn(session)
        return msg

    def _handle_pickup(self, session: GameSession, pos: Pos) -> str:
        """Подбирает предмет на клетке ``pos``, если он есть.

        Args:
            session (GameSession): Текущая игровая сессия.
            pos (Pos): Клетка, на которую наступил герой.

        Returns:
            str: Сообщение о подборе/полном рюкзаке или пустую строку.
        """
        found_item = session.level.items.pop((pos.x, pos.y), None)
        if found_item is None:
            return ""
        if session.backpack.add_item(found_item):
            if found_item.item_type == ItemType.TREASURE:
                session.stats.treasure = session.backpack.treasure
            if found_item.item_type == ItemType.KEY:
                return f"Picked up {found_item.name} key."
            return f"Picked up {found_item.name}."
        session.level.items[(pos.x, pos.y)] = found_item
        return f"Backpack slot for {found_item.item_type.value} is full."

    def _advance_level(self, session: GameSession) -> str:
        """Переводит игрока на следующий уровень или завершает игру победой.

        Args:
            session (GameSession): Текущая игровая сессия (мутируется).

        Returns:
            str: Строка-сообщение о переходе на следующий уровень или победе.
        """
        if session.current_level >= 21:
            session.victory = True
            session.game_over = True
            session.stats.reached_level = 21
            return "You escaped the final dungeon level. Victory!"

        session.current_level += 1
        session.stats.reached_level = session.current_level

        session.difficulty_bias = self._update_difficulty_bias(session)  # Автобаланс
        session.backpack.keys.clear()

        level, start_pos = generate_level(
            session.current_level,
            self.rng,
            difficulty_bias=session.difficulty_bias,
        )
        session.level = level
        session.hero_pos = start_pos
        visible = compute_visible_cells(session)
        update_discovery(session, visible)
        session.last_visible = visible
        session.level_damage_taken = 0
        return f"You descend to level {session.current_level}."

    def _end_turn(self, session: GameSession) -> None:
        if session.game_over:
            return

        revealed_mimic = self._reveal_mimics(session)

        enemy_turn(self.rng, session)

        if not session.game_over:
            self._tick_effects(session)

        visible = compute_visible_cells(session)
        update_discovery(session, visible)
        session.last_visible = visible

        if revealed_mimic:
            session.flash_message = "The item suddenly transforms into a Mimic!"    

    def _reveal_mimics(self, session: GameSession) -> bool:
        """Раскрывает мимиков рядом с игроком.
        Возвращает True, если хоть один раскрылся.
        """
        hero = session.hero_pos
        revealed = False

        for enemy in session.level.enemies.values():
            if (enemy.enemy_type == EnemyType.MIMIC 
                and enemy.disguised):
                dx = abs(enemy.pos.x - hero.x)
                dy = abs(enemy.pos.y - hero.y)
                if dx <= 1 and dy <= 1:
                    enemy.disguised = False
                    revealed = True

        return revealed

                    
    def _tick_effects(self, session: GameSession) -> None:
        """Уменьшает счётчики активных эффектов и снимает истёкшие.

        Для каждого истёкшего эффекта применяет обратный дельта
        (``-effect.value``) к соответствующей характеристике. Если
        после снятия эффекта HP становится ≤ 0, оно устанавливается
        в 1.

        Args:
            session (GameSession): Текущая игровая сессия (мутируется).
        """
        expired: list[ActiveEffect] = []
        for effect in session.active_effects:
            effect.turns_left -= 1
            if effect.turns_left <= 0:
                expired.append(effect)

        for effect in expired:
            session.active_effects.remove(effect)
            self._apply_stat_delta(session, effect.stat, -effect.value)

        # После истечения эликсира HP не может упасть до 0
        if session.hero.hp <= 0:
            session.hero.hp = 1

    def _apply_stat_delta(self, session: GameSession, stat: StatType | None, delta: int,) -> None:
        """Изменяет одну характеристику героя на ``delta``.

        Используется для применения и отмены эффектов эликсиров и свитков.
        Характеристики не опускаются ниже 1. При изменении ``MAX_HP``
        текущий HP масштабируется вместе с максимумом.

        Args:
            session (GameSession): Текущая игровая сессия (мутируется).
            stat (StatType): Изменяемая характеристика. Если ``None`` — вызов игнорируется.
            delta (int): Изменение (положительное — увеличение, отрицательное — уменьшение).
        """
        if stat is None:
            return

        hero = session.hero

        if stat == StatType.AGILITY:
            hero.agility = max(1, hero.agility + delta)

        elif stat == StatType.STRENGTH:
            hero.strength = max(1, hero.strength + delta)

        elif stat == StatType.MAX_HP:
            hero.max_hp = max(1, hero.max_hp + delta)
            hero.hp += delta                     # HP меняется вместе с максимумом
            hero.hp = max(1, hero.hp)
            hero.hp = min(hero.hp, hero.max_hp)  # не превышает новый максимум

        elif stat == StatType.HP:
            hero.hp = min(hero.max_hp, max(1, hero.hp + delta))

    def _update_difficulty_bias(self, session: GameSession) -> int:
        """Оценивает результат прошедшего уровня и корректирует сложность.

        Метрика — доля HP, потерянных за уровень относительно max_hp:
        - < 20% потерь → игрок справляется легко → сложность +1
        - 20–50% потерь → нормально → сложность не меняется
        - > 50% потерь → игроку тяжело → сложность -1
        - > 70% потерь → очень тяжело → сложность -2

        Диапазон bias ограничен [-3, +5] чтобы избежать экстремумов.

        Args:
            session (GameSession): Текущая сессия. Читает ``level_damage_taken``
                                   и ``hero.max_hp``.

        Returns:
            int: Новое значение ``difficulty_bias``.
        """
        lost_ratio = session.level_damage_taken / max(1, session.hero.max_hp)

        current = session.difficulty_bias
        if lost_ratio < 0.20:
            delta = +1   # слишком легко
        elif lost_ratio > 0.70:
            delta = -2   # очень тяжело
        elif lost_ratio > 0.50:
            delta = -1   # тяжело
        else:
            delta = 0    # норма

        return max(-3, min(5, current + delta))

    def _free_adjacent_pos(self, level: Level, pos: Pos) -> Pos | None:
        """Находит свободную клетку рядом с ``pos`` для выброса предмета.

        Перебирает 8 соседних клеток (4 кардинальных + 4 диагональных)
        в случайном порядке. Клетка считается свободной, если она проходима
        и на ней нет другого предмета или врага.

        Args:
            level (Level): Текущий уровень.
            pos (Pos): Центральная позиция (обычно позиция героя).

        Returns:
            Pos | None: Первая найденная свободная клетка или ``None``, если все
                        8 соседних клеток заняты.
        """
        candidates = [pos + d for d in CARDINAL_DIRS + DIAGONAL_DIRS]
        self.rng.shuffle(candidates)
        for c in candidates:
            if not level.is_walkable(c):
                continue
            if (c.x, c.y) in level.items:
                continue
            if any(enemy.pos == c for enemy in level.enemies.values()):
                continue
            return c
        return None
