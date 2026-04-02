"""Фабрика противников.

Модуль отвечает за создание экземпляров врагов с правильными
характеристиками и за формирование пула доступных типов врагов
в зависимости от глубины уровня.

Характеристики противников:
    Zombie      низкая ловкость; средняя сила, враждебность; высокое здоровье.
    Vampire     высокая ловкость, враждебность и здоровье; средняя сила.
    Ghost       высокая ловкость; низкая сила, враждебность и здоровье.
    Ogre        очень высокая сила и здоровье; низкая ловкость; средняя враждебность.
    Snake_Mage  очень высокая ловкость; высокая враждебность.
    Mimic       высокая ловкость и здоровье ; низкая враждебность и сила; имитирует предметы
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from domain.models import Enemy, EnemyType, Pos


@dataclass(frozen=True, slots=True)
class _EnemyTemplate:
    """Базовые (уровень 0) характеристики типа врага.

    Поля ``hp_base``, ``agility_base``, ``strength_base`` — значения
    при ``level_index == 0`` (до применения масштабирования).
    Поля ``hp_scale``, ``strength_scale`` — прибавка за каждые 3 уровня.

    Attributes:
        symbol (str):         Символ отображения в curses.
        color (int):          Индекс цветовой пары curses (см. _init_colors).
        hp_base (int):        Базовое максимальное здоровье.
        hp_scale (int):       Прирост hp на единицу lvl_bonus (level_index // 3).
        agility_base (int):   Базовая ловкость.
        strength_base (int):  Базовая сила.
        strength_scale (int): Прирост силы на единицу lvl_bonus.
        hostility (int):      Радиус обнаружения игрока (постоянный).
    """
    symbol: str
    color: int
    hp_base: int
    hp_scale: int
    agility_base: int
    strength_base: int
    strength_scale: int
    hostility: int


# Таблица шаблонов.
#
# Цвета (curses color_pair индексы, см. CursesApp._init_colors):
#   1=RED  2=GREEN  3=YELLOW  4=CYAN  5=WHITE  6=BLUE  7=MAGENTA
_TEMPLATES: dict[EnemyType, _EnemyTemplate] = {
    EnemyType.ZOMBIE: _EnemyTemplate(
        symbol="z", color=2,        # GREEN (зелёный z)
        hp_base=10, hp_scale=3,
        agility_base=5, strength_base=6, strength_scale=1,
        hostility=7,
    ),
    EnemyType.VAMPIRE: _EnemyTemplate(
        symbol="v", color=1,        # RED (красная v)
        hp_base=14, hp_scale=3,
        agility_base=10, strength_base=8, strength_scale=1,
        hostility=10,
    ),
    EnemyType.GHOST: _EnemyTemplate(
        symbol="g", color=5,        # WHITE (белый g)
        hp_base=8,  hp_scale=2,
        agility_base=8, strength_base=3, strength_scale=1,
        hostility=5,
    ),
    EnemyType.OGRE: _EnemyTemplate(
        symbol="O", color=3,        # YELLOW (жёлтый O)
        hp_base=22, hp_scale=4,
        agility_base=4, strength_base=12, strength_scale=2,
        hostility=8,
    ),
    EnemyType.SNAKE_MAGE: _EnemyTemplate(
        symbol="s", color=5,        # WHITE  (белая s)
        hp_base=12, hp_scale=2,
        agility_base=13, strength_base=7, strength_scale=1,
        hostility=10,
    ),
    EnemyType.MIMIC: _EnemyTemplate(
    symbol="m", color=5,            # WHITE (белый g)
    hp_base=16, hp_scale=3,
    agility_base=11, strength_base=4, strength_scale=1,
    hostility=3,
),
}


def create_enemy(enemy_id: int, enemy_type: EnemyType, level_index: int,
                 rng: random.Random) -> Enemy:
    """Создаёт экземпляр врага с характеристиками, масштабированными по уровню.

    Args:
        enemy_id (int): Уникальный идентификатор врага в пределах уровня.
        enemy_type (EnemyType): Тип создаваемого врага.
        level_index (int): Индекс уровня подземелья (1–21). Влияет на сложность.
        rng (random.Random): Генератор случайных чисел для разброса характеристик.

    Returns:
        Enemy: Новый экземпляр ``Enemy`` с позицией ``Pos(1, 1)`` — генератор
               уровня обязан установить ``enemy.pos`` после вызова этой функции.

    Raises:
        KeyError: Если ``enemy_type`` не найден в ``_TEMPLATES`` (не должно
                  происходить при корректных значениях ``EnemyType``).
    """
    template = _TEMPLATES[enemy_type]
    lvl_bonus = max(-2, level_index // 3)

    hp = template.hp_base + lvl_bonus * template.hp_scale
    agility = template.agility_base + lvl_bonus
    strength = template.strength_base + lvl_bonus * template.strength_scale

    # Небольшой разброс: каждый враг немного уникален
    hp = max(1, hp + rng.randint(-1, 1))
    agility = max(1, agility + rng.randint(-1, 1))
    strength = max(1, strength + rng.randint(-1, 1))

    extra: dict = {}
    if enemy_type == EnemyType.SNAKE_MAGE:
        extra["snake_diag_dir"] = (1, 1)

    if enemy_type == EnemyType.MIMIC:
        fake_items = ["!", "?", "/", "$"]   # зелье, свиток, оружие, сокровище
        extra["disguised"] = True
        extra["mimic_item_symbol"] = rng.choice(fake_items)

    return Enemy(
        enemy_id=enemy_id,
        enemy_type=enemy_type,
        pos=Pos(1, 1),
        max_hp=hp,
        hp=hp,
        agility=agility,
        strength=strength,
        hostility=template.hostility,
        symbol=template.symbol,
        color=template.color,
        **extra,
    )


def enemy_pool_by_depth(level_index: int) -> list[EnemyType]:
    """Возвращает список типов врагов, доступных на данной глубине.

    Пул расширяется по мере углубления в подземелье — более опасные
    враги появляются только на достаточно глубоких уровнях. На глубоких
    уровнях (>= 8) Огры и Змеи-маги добавляются повторно, увеличивая
    их долю в спавне.

    Args:
        level_index (int): Индекс уровня (1–21, с учётом баланса).

    Returns:
        list[EnemyType]: Список ``EnemyType``, из которого генератор случайно выбирает
                         тип для каждого врага. Может содержать дубликаты — это намеренно,
                         дубликаты увеличивают вероятность выпадения типа.

    Examples:
        >>> enemy_pool_by_depth(1)
        [<EnemyType.ZOMBIE: 'zombie'>, <EnemyType.GHOST: 'ghost'>]
        >>> len(enemy_pool_by_depth(10))  # Ogre и Snake_Mage удвоены
        7
    """
    pool: list[EnemyType] = [EnemyType.ZOMBIE, EnemyType.GHOST]
    if level_index >= 2:
        pool.append(EnemyType.VAMPIRE)
    if level_index >= 3:
        pool.append(EnemyType.OGRE)
    if level_index >= 4:
        pool.append(EnemyType.SNAKE_MAGE)
    if level_index >= 5:
        pool.append(EnemyType.MIMIC)
    if level_index >= 8:
        # Увеличиваем долю сильных врагов на глубоких уровнях
        pool.extend([EnemyType.OGRE, EnemyType.SNAKE_MAGE])

    return pool
