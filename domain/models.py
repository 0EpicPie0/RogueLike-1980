"""Доменные модели игры Rogue.

Сериализация:
    Каждый класс реализует ``to_dict() -> dict`` и ``from_dict(data) -> T``.
    Формат совместим с JSON через ``json.dumps`` / ``json.loads``.
    Поле ``GameSession.last_visible`` намеренно не сериализуется — оно
    пересчитывается при каждом ходу и не является частью сохранения.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def _rng_state_to_json(
    state: tuple[int, tuple[int, ...], float | None] | None,
) -> list | None:
    """Конвертирует состояние ``random.Random`` в JSON-совместимый список.

    ``random.Random.getstate()`` возвращает 3-кортеж:
    ``(version: int, internalstate: tuple[int, ...], gauss_next: float | None)``.
    Внутренний кортеж содержит 625 элементов — сериализуем его как список.

    Args:
        state (tuple[int, tuple[int, ...], float | None] | None): 
        Результат ``rng.getstate()`` или ``None``.

    Returns:
        list | None: ``[version, [int, ...], gauss_next]`` или ``None``.
    """
    if state is None:
        return None
    version, internalstate, gauss_next = state
    return [version, list(internalstate), gauss_next]


def _rng_state_from_json(
    raw: list | None,
) -> tuple[int, tuple[int, ...], float | None] | None:
    """Восстанавливает состояние ``random.Random`` из JSON-списка.

    Обратная операция к :func:`_rng_state_to_json`.

    Args:
        raw (list | None): Список ``[version, [int, ...], gauss_next]`` или ``None``.

    Returns:
        tuple[int, tuple[int, ...], float | None] | None:
        3-кортеж, пригодный для ``rng.setstate()``, или ``None``.
    """
    if not raw:
        return None
    version = int(raw[0])
    internalstate = tuple(int(x) for x in raw[1])
    gauss_next = float(raw[2]) if raw[2] is not None else None
    return (version, internalstate, gauss_next)


class Tile(str, Enum):
    """"Типы клеток карты.

    Наследует ``str`` для прямого сравнения с символами тайлов в двумерном массиве ``Level.tiles``.
    """
    WALL = "#"
    FLOOR = "."
    EXIT = ">"
    DOOR_RED = "R"
    DOOR_BLUE = "B"
    DOOR_YELLOW = "Y"


class ItemType(str, Enum):
    """Основные типы предметов.

    Определяет категорию предмета и слот рюкзака, в котором он хранится.
    """
    TREASURE = "treasure"
    FOOD = "food"
    ELIXIR = "elixir"
    SCROLL = "scroll"
    WEAPON = "weapon"
    KEY = "key"


class StatType(str, Enum):
    """Характеристики персонажа, которые могут изменять предметы и эффекты."""
    HP = "hp"
    MAX_HP = "max_hp"
    AGILITY = "agility"
    STRENGTH = "strength"


class EnemyType(str, Enum):
    """Типы противников. Определяет паттерн поведения и спецэффекты."""
    ZOMBIE = "zombie"
    VAMPIRE = "vampire"
    GHOST = "ghost"
    OGRE = "ogre"
    SNAKE_MAGE = "snake_mage"
    MIMIC = "mimic"


@dataclass(slots=True, frozen=True)
class Pos:
    """Неизменяемая позиция на карте подземелья.

    ``frozen=True`` гарантирует хешируемость — позиции используются
    как ключи в ``set[tuple[int,int]]`` и ``dict[tuple[int,int], Item]``.
    Для этого сам ``Pos`` хранится в кортежах ``(x, y)``, а не напрямую.

    Attributes:
        x (int): Горизонтальная координата (0 — левый край).
        y (int): Вертикальная координата (0 — верхний край).
    """
    x: int
    y: int

    def __add__(self, other: tuple[int, int]) -> "Pos":
        """Сдвигает позицию на вектор ``(dx, dy)``.

        Args:
            other (tuple[int, int])): Кортеж смещения ``(dx, dy)``.

        Returns:
            "Pos": Новая позиция ``Pos(x + dx, y + dy)``.
        """
        return Pos(self.x + other[0], self.y + other[1])


@dataclass(slots=True)
class Item:
    """Предмет в подземелье или в рюкзаке героя.

    Одна структура описывает все типы предметов. Интерпретация полей
    зависит от ``item_type``:

    - TREASURE: ``value`` — стоимость. Не лежит в слотах рюкзака,
                накапливается в ``Backpack.treasure``.
    - FOOD:     ``value`` — восстанавливаемое HP.
    - ELIXIR:   ``stat`` — изменяемая характеристика; ``value`` — величина;
                ``duration`` — длительность в ходах.
    - SCROLL:   ``stat`` — изменяемая характеристика; ``value`` — величина;
                эффект постоянный (``duration`` не используется).
    - WEAPON:   ``value`` — бонус силы; ``subtype`` — название оружия
                (dagger / mace / axe / spear).

    Attributes:
        item_type (ItemType): Категория предмета.
        name (str): Человекочитаемое название (напр. «elixir of agility»).
        value (int): Числовое значение эффекта (HP, бонус силы, стоимость).
        stat (StatType | None): Изменяемая характеристика (для эликсиров, свитков).
        duration (int): Длительность временного эффекта в ходах (только ELIXIR).
        subtype (str): Подтип предмета (ТЗ §«Предмет»). Для оружия — тип клинка;
                       для прочих предметов совпадает с ``stat.value`` или пуст.
    """
    item_type: ItemType
    name: str
    value: int
    stat: StatType | None = None
    duration: int = 0
    subtype: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Возвращает словарь статов предмета"""
        return {
            "item_type": self.item_type.value,
            "name":      self.name,
            "value":     self.value,
            "stat":      self.stat.value if self.stat else None,
            "duration":  self.duration,
            "subtype":   self.subtype,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Item":
        """Распаковывает словарь статов предмета"""
        stat = StatType(data["stat"]) if data.get("stat") else None
        return Item(
            item_type=ItemType(data["item_type"]),
            name=str(data["name"]),
            value=int(data["value"]),
            stat=stat,
            duration=int(data.get("duration", 0)),
            subtype=str(data.get("subtype", "")),
        )


@dataclass(slots=True)
class ActiveEffect:
    """Временный эффект от выпитого эликсира.

    Хранится в ``GameSession.active_effects``. Каждый ход ``GameEngine``
    декрементирует ``turns_left`` и при достижении нуля снимает эффект,
    применяя обратный дельта к соответствующей характеристике героя.

    Attributes:
        stat (StatType): Характеристика, которую изменяет эффект.
        value (int): Величина изменения (будет вычтена при истечении).
        turns_left (int): Оставшееся количество ходов.
    """
    stat: StatType
    value: int
    turns_left: int

    def to_dict(self) -> dict[str, Any]:
        """Cловарь со статами эликсира"""
        return {
            "stat":       self.stat.value,
            "value":      self.value,
            "turns_left": self.turns_left,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ActiveEffect":
        """Распаковывает словарь статов эликсира"""
        return ActiveEffect(
            stat=StatType(data["stat"]),
            value=int(data["value"]),
            turns_left=int(data["turns_left"]),
        )


# ---------------------------------------------------------------------------
# Backpack
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Backpack:
    """Инвентарь героя.

    Каждый тип предмета хранится в отдельном слоте (списке) ёмкостью 9.
    Сокровища не занимают слот — они суммируются в ``treasure``.

    Attributes:
        food (list[Item]):     Список предметов типа FOOD (макс. 9).
        elixir (list[Item]):   Список предметов типа ELIXIR (макс. 9).
        scroll (list[Item]):   Список предметов типа SCROLL (макс. 9).
        weapon (list[Item]):   Список предметов типа WEAPON (макс. 9).
        treasure (list[Item]): Суммарная стоимость всех подобранных сокровищ.
    """
    food:     list[Item] = field(default_factory=list)
    elixir:   list[Item] = field(default_factory=list)
    scroll:   list[Item] = field(default_factory=list)
    weapon:   list[Item] = field(default_factory=list)
    treasure: int = 0
    keys: list[str] = field(default_factory=list)

    _max_slot_size: int = field(default=9, init=False, repr=False)

    def add_item(self, item: Item) -> bool:
        """Добавляет предмет в рюкзак.

        Сокровища (TREASURE) суммируются в ``treasure`` и всегда
        принимаются. Остальные предметы добавляются в соответствующий
        слот, если он не заполнен.

        Args:
            item (Item): Предмет для добавления.

        Returns:
            bool: ``True`` если предмет добавлен, ``False`` если слот полон.
        """
        if item.item_type == ItemType.TREASURE:
            self.treasure += item.value
            return True
        if item.item_type == ItemType.KEY:
            self.keys.append(item.name)
            return True

        slot = self.bucket(item.item_type)
        if len(slot) >= self._max_slot_size:
            return False
        slot.append(item)
        return True

    def bucket(self, item_type: ItemType) -> list[Item]:
        """Возвращает список предметов указанного типа.

        Args:
            item_type: Тип предмета. TREASURE не имеет слота — при передаче
                       этого значения будет поднят ``ValueError``.

        Returns:
            Мутируемый список предметов данного типа.

        Raises:
            ValueError: Если передан ``ItemType.TREASURE`` или неизвестный тип.
        """
        match item_type:
            case ItemType.FOOD: return self.food
            case ItemType.ELIXIR: return self.elixir
            case ItemType.SCROLL: return self.scroll
            case ItemType.WEAPON: return self.weapon
            case _:
                raise ValueError(
                    f"No inventory slot for item type: {item_type!r}")

    def to_dict(self) -> dict[str, Any]:
        """Словарь рюкзака"""
        return {
            "food":     [item.to_dict() for item in self.food],
            "elixir":   [item.to_dict() for item in self.elixir],
            "scroll":   [item.to_dict() for item in self.scroll],
            "weapon":   [item.to_dict() for item in self.weapon],
            "treasure": self.treasure,
            "keys": list(self.keys),
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Backpack":
        """Распаковка словаря рюкзака"""
        return Backpack(
            food=[Item.from_dict(x) for x in data.get("food",    [])],
            elixir=[Item.from_dict(x) for x in data.get("elixir",  [])],
            scroll=[Item.from_dict(x) for x in data.get("scroll",  [])],
            weapon=[Item.from_dict(x) for x in data.get("weapon",  [])],
            treasure=int(data.get("treasure", 0)),
            keys=list(data.get("keys", [])),
        )


@dataclass(slots=True)
class Character:
    """Игровой персонаж (герой).

    Attributes:
        max_hp (int):   Максимальный уровень здоровья. Может быть уменьшен
                        атаками Вампира или увеличен свитками/эликсирами.
        hp (int):       Текущее здоровье. При hp <= 0 игра заканчивается.
        agility (int):  Ловкость. Участвует в формуле вероятности попадания
                        (см. ``combat.attack_hits``).
        strength (int): Сила. Определяет базовый урон без оружия и с оружием
                        (см. ``combat.compute_damage``).
        weapon (Item | None): Текущее снаряжённое оружие или ``None`` (безоружный бой).
    """
    max_hp:   int
    hp:       int
    agility:  int
    strength: int
    weapon:   Item | None = None

    def alive(self) -> bool:
        """Возвращает ``True``, если герой жив (hp > 0)."""
        return self.hp > 0

    def to_dict(self) -> dict[str, Any]:
        """Словарь статов"""
        return {
            "max_hp":   self.max_hp,
            "hp":       self.hp,
            "agility":  self.agility,
            "strength": self.strength,
            "weapon":   self.weapon.to_dict() if self.weapon else None,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Character":
        """Распаковка словаря статов"""
        weapon_data = data.get("weapon")
        return Character(
            max_hp=int(data["max_hp"]),
            hp=int(data["hp"]),
            agility=int(data["agility"]),
            strength=int(data["strength"]),
            weapon=Item.from_dict(weapon_data) if weapon_data else None,
        )


@dataclass(slots=True)
class Enemy:
    """Противник на уровне подземелья.

    Содержит как общие для всех врагов поля (характеристики, позиция),
    так и специфичные для конкретных типов флаги состояния.

    Attributes:
        enemy_id (int):   Уникальный ID в пределах уровня (ключ в ``Level.enemies``).
        enemy_type (EnemyType): Тип врага — определяет паттерн поведения и спецэффекты.
        pos (Pos):        Текущая позиция на карте.
        max_hp (int):     Максимальное здоровье.
        hp (int):         Текущее здоровье. При hp <= 0 враг погибает.
        agility (int):    Ловкость — участвует в формуле попадания.
        strength (int):   Сила — участвует в формуле урона.
        hostility (int):  Радиус обнаружения игрока (манхэттенское расстояние).
                          Если игрок ближе — враг начинает преследование.
        symbol (str):     Символ отображения в curses (z, v, g, O, s).
        color (int):      Индекс цветовой пары curses (см. ``CursesApp._init_colors``).

        ghost_visible (bool): Видим ли Ghost в данный момент. False = невидим, пока не вступил в бой.
        ghost_engaged (bool): True после первого боевого контакта с Призраком.
        ogre_rest_turns (int): Ходов отдыха после атаки (для Огра).
        ogre_guaranteed_hit (bool): True — следующая атака Огра гарантированно попадает.
        snake_diag_dir (tuple[int, int]): Текущее диагональное направление патруля Змея-мага.
        vampire_first_hit_block (bool): True — первый удар по Вампиру всегда промах.
    """
    enemy_id:   int
    enemy_type: EnemyType
    pos:        Pos
    max_hp:     int
    hp:         int
    agility:    int
    strength:   int
    hostility:  int
    symbol:     str
    color:      int

    ghost_visible:  bool = True
    ghost_engaged:  bool = False   # BUG-6 fix

    ogre_rest_turns:     int = 0
    ogre_guaranteed_hit: bool = False

    snake_diag_dir: tuple[int, int] = (1, 1)

    vampire_first_hit_block: bool = True

    # для особых механик мимика
    disguised: bool = False
    mimic_item_symbol: str | None = None

    def alive(self) -> bool:
        """Возвращает ``True``, если враг жив (hp > 0)."""
        return self.hp > 0

    def to_dict(self) -> dict[str, Any]:
        """Словарь статов врага"""
        return {
            "enemy_id":               self.enemy_id,
            "enemy_type":             self.enemy_type.value,
            "pos":                    [self.pos.x, self.pos.y],
            "max_hp":                 self.max_hp,
            "hp":                     self.hp,
            "agility":                self.agility,
            "strength":               self.strength,
            "hostility":              self.hostility,
            "symbol":                 self.symbol,
            "color":                  self.color,
            "ghost_visible":          self.ghost_visible,
            "ghost_engaged":          self.ghost_engaged,
            "ogre_rest_turns":        self.ogre_rest_turns,
            "ogre_guaranteed_hit":    self.ogre_guaranteed_hit,
            "snake_diag_dir":         list(self.snake_diag_dir),
            "vampire_first_hit_block": self.vampire_first_hit_block,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Enemy":
        """Распаковка статов врага"""
        diag = data.get("snake_diag_dir", [1, 1])
        return Enemy(
            enemy_id=int(data["enemy_id"]),
            enemy_type=EnemyType(data["enemy_type"]),
            pos=Pos(int(data["pos"][0]), int(data["pos"][1])),
            max_hp=int(data["max_hp"]),
            hp=int(data["hp"]),
            agility=int(data["agility"]),
            strength=int(data["strength"]),
            hostility=int(data["hostility"]),
            symbol=str(data["symbol"]),
            color=int(data["color"]),
            ghost_visible=bool(data.get("ghost_visible",          True)),
            ghost_engaged=bool(data.get("ghost_engaged",          False)),
            ogre_rest_turns=int(data.get("ogre_rest_turns",        0)),
            ogre_guaranteed_hit=bool(
                data.get("ogre_guaranteed_hit",    False)),
            snake_diag_dir=(int(diag[0]), int(diag[1])),
            vampire_first_hit_block=bool(
                data.get("vampire_first_hit_block", True)),
        )


@dataclass(slots=True)
class Room:
    """Прямоугольная комната уровня подземелья.

    Координаты задают ограничивающий прямоугольник включительно.
    Граничные клетки ``(x1, y1)–(x2, y2)`` являются стенами;
    внутренние ``(x1+1, y1+1)–(x2-1, y2-1)`` — полом.

    Attributes:
        room_id (int):    Уникальный ID комнаты на уровне (0–8).
        x1, y1 (int):     Верхний левый угол (стена).
        x2, y2 (int):     Нижний правый угол (стена).
        discovered (bool): True если комната уже попадала в поле зрения героя.
                    Только открытые комнаты отображаются в тумане войны.
    """
    room_id:    int
    x1:         int
    y1:         int
    x2:         int
    y2:         int
    discovered: bool = False

    def contains(self, pos: Pos) -> bool:
        """Проверяет, находится ли позиция внутри комнаты (включая стены).

        Args:
            pos (Pos): Проверяемая позиция.

        Returns:
            bool: ``True`` если ``x1 <= pos.x <= x2`` и ``y1 <= pos.y <= y2``.
        """
        return self.x1 <= pos.x <= self.x2 and self.y1 <= pos.y <= self.y2

    def center(self) -> Pos:
        """Используется как точка привязки при прокладке коридоров.

        Returns:
            Pos: Центральная клетка комнаты.
        """
        return Pos((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)

    def random_floor_cells(self) -> list[Pos]:
        """Возвращает все клетки пола комнаты (без стен).

        Клетки пола — это внутренние клетки: ``x in (x1+1..x2-1)``,
        ``y in (y1+1..y2-1)``.

        Returns:
            list[Pos]: Список всех проходимых клеток комнаты.
        """
        return [
            Pos(x, y)
            for y in range(self.y1 + 1, self.y2)
            for x in range(self.x1 + 1, self.x2)
        ]

    def to_dict(self) -> dict[str, Any]:
        """Аргументы комнаты"""
        return {
            "room_id":    self.room_id,
            "x1":         self.x1,
            "y1":         self.y1,
            "x2":         self.x2,
            "y2":         self.y2,
            "discovered": self.discovered,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Room":
        """Распаковка аргументов комнаты"""
        return Room(
            room_id=int(data["room_id"]),
            x1=int(data["x1"]),
            y1=int(data["y1"]),
            x2=int(data["x2"]),
            y2=int(data["y2"]),
            discovered=bool(data.get("discovered", False)),
        )


@dataclass(slots=True)
class Level:
    """Один уровень подземелья.

    Attributes:
        index (int):    Номер уровня (1–21).
        width (int):    Ширина карты в клетках.
        height (int):   Высота карты в клетках.
        tiles (list[list[str]]):    Двумерный массив тайлов ``tiles[y][x]``.
                  Значения — строки из ``Tile`` enum.
        rooms (list[Room]):    Список из 9 комнат.
        corridors (list[list[Pos]]): Список коридоров; каждый коридор — список клеток ``Pos``.
        start_room_id (int): ID стартовой комнаты (без врагов и предметов).
        exit_pos (Pos): Позиция выхода на следующий уровень.
        items (dict[tuple[int, int], Item]):    Словарь ``(x, y) -> Item`` предметов на полу.
        enemies (dict[int, Enemy] ):  Словарь ``enemy_id -> Enemy`` живых врагов.
        discovered_corridors (set[tuple[int, int]]): Множество ``(x, y)`` клеток коридоров,
                                                    уже попадавших в поле зрения (туман войны).
    """
    index:    int
    width:    int
    height:   int
    tiles:    list[list[str]]
    rooms:    list[Room]
    corridors: list[list[Pos]]
    start_room_id: int
    exit_pos: Pos
    items:    dict[tuple[int, int], Item] = field(default_factory=dict)
    enemies:  dict[int, Enemy] = field(default_factory=dict)
    discovered_corridors: set[tuple[int, int]] = field(default_factory=set)

    # Кеш для ускорения room_for(): строится лениво при первом вызове.
    # Не сериализуется — восстанавливается автоматически.
    _room_cache: dict[tuple[int, int], Room | None] = field(
        default_factory=dict, init=False, repr=False,
    )

    def tile_at(self, pos: Pos) -> str:
        """Возвращает тайл на позиции ``pos``.

        Args:
            pos (Pos): Позиция на карте.

        Returns:
            str: Строковый символ тайла (``"#"``, ``"."``, ``">"``).
        """
        return self.tiles[pos.y][pos.x]

    def set_tile(self, pos: Pos, tile: str) -> None:
        """Устанавливает тайл на позиции ``pos``.

        Args:
            pos (Pos): Позиция на карте.
            tile (str): Новое значение тайла (строка из ``Tile`` enum).
        """
        self.tiles[pos.y][pos.x] = tile

    def is_inside(self, pos: Pos) -> bool:
        """Проверяет, находится ли позиция в пределах карты.

        Args:
            pos (Pos): Проверяемая позиция.

        Returns:
           bool: ``True`` если ``0 <= pos.x < width`` и ``0 <= pos.y < height``.
        """
        return 0 <= pos.x < self.width and 0 <= pos.y < self.height

    def is_walkable(self, pos: Pos) -> bool:
        """Проверяет, можно ли зайти на клетку.

        Клетка проходима если она находится внутри карты и является
        полом (``FLOOR``) или выходом (``EXIT``). Стены — непроходимы.

        Args:
            pos (Pos): Проверяемая позиция.

        Returns:
            bool: ``True`` если клетка проходима.
        """
        if not self.is_inside(pos):
            return False
        return self.tile_at(pos) in {Tile.FLOOR.value, Tile.EXIT.value}

    def room_for(self, pos: Pos) -> Room | None:
        """Возвращает комнату, содержащую позицию, или ``None`` для коридора.

        Результат кешируется после первого обращения — метод вызывается
        в горячих путях рендерера (на каждую видимую клетку), поэтому
        линейный поиск по 9 комнатам был бы заметен при частом вызове.

        Args:
            pos (Pos): Позиция на карте.

        Returns:
            Room | None: Комната, содержащая ``pos``, или ``None`` если ``pos``
                         находится в коридоре или за пределами карты.
        """
        key = (pos.x, pos.y)
        if key not in self._room_cache:
            self._room_cache[key] = next(
                (room for room in self.rooms if room.contains(pos)), None
            )
        return self._room_cache[key]

    def to_dict(self) -> dict[str, Any]:
        """Статы комнаты"""
        return {
            "index":        self.index,
            "width":        self.width,
            "height":       self.height,
            "tiles":        self.tiles,
            "rooms":        [room.to_dict() for room in self.rooms],
            "corridors":    [[[p.x, p.y] for p in c] for c in self.corridors],
            "start_room_id": self.start_room_id,
            "exit_pos":     [self.exit_pos.x, self.exit_pos.y],
            "items":        {
                f"{x},{y}": item.to_dict()
                for (x, y), item in self.items.items()
            },
            "enemies": {
                str(k): enemy.to_dict()
                for k, enemy in self.enemies.items()
            },
            "discovered_corridors": [list(p) for p in self.discovered_corridors],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Level":
        """Распаковка статов комнаты"""
        items: dict[tuple[int, int], Item] = {
            (int(sx), int(sy)): Item.from_dict(raw)
            for key, raw in data.get("items", {}).items()
            for sx, sy in [key.split(",")]
        }
        enemies = {
            int(eid): Enemy.from_dict(raw)
            for eid, raw in data.get("enemies", {}).items()
        }
        corridors = [
            [Pos(int(p[0]), int(p[1])) for p in corridor]
            for corridor in data.get("corridors", [])
        ]
        discovered_corridors = {
            (int(pt[0]), int(pt[1]))
            for pt in data.get("discovered_corridors", [])
        }
        return Level(
            index=int(data["index"]),
            width=int(data["width"]),
            height=int(data["height"]),
            tiles=[[str(c) for c in row] for row in data["tiles"]],
            rooms=[Room.from_dict(r) for r in data["rooms"]],
            corridors=corridors,
            start_room_id=int(data["start_room_id"]),
            exit_pos=Pos(int(data["exit_pos"][0]), int(data["exit_pos"][1])),
            items=items,
            enemies=enemies,
            discovered_corridors=discovered_corridors,
        )


@dataclass(slots=True)
class RunStats:
    """Статистика одного прохождения игры.

    Используется в таблице рекордов (отсортирована по ``treasure``).
    Все поля накапливаются в процессе игры и сохраняются при завершении.

    Attributes:
        treasure (int):     Суммарное количество собранных сокровищ.
        reached_level (int): Максимальный достигнутый уровень (1–21).
        kills (int):        Количество побеждённых противников.
        food_used (int):    Количество съеденных единиц еды.
        elixirs_used (int): Количество выпитых эликсиров.
        scrolls_used (int): Количество прочитанных свитков.
        hits_dealt (int):   Количество успешных ударов по врагам.
        hits_taken (int):   Количество успешных ударов по герою.
        steps (int):        Количество пройденных клеток.
    """
    treasure:     int = 0
    reached_level: int = 1
    kills:        int = 0
    food_used:    int = 0
    elixirs_used: int = 0
    scrolls_used: int = 0
    hits_dealt:   int = 0
    hits_taken:   int = 0
    steps:        int = 0

    def to_dict(self) -> dict[str, Any]:
        """Статистика"""
        return {
            "treasure":     self.treasure,
            "reached_level": self.reached_level,
            "kills":        self.kills,
            "food_used":    self.food_used,
            "elixirs_used": self.elixirs_used,
            "scrolls_used": self.scrolls_used,
            "hits_dealt":   self.hits_dealt,
            "hits_taken":   self.hits_taken,
            "steps":        self.steps,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "RunStats":
        """Статистика"""
        return RunStats(
            treasure=int(data.get("treasure",      0)),
            reached_level=int(data.get("reached_level", 1)),
            kills=int(data.get("kills",         0)),
            food_used=int(data.get("food_used",     0)),
            elixirs_used=int(data.get("elixirs_used",  0)),
            scrolls_used=int(data.get("scrolls_used",  0)),
            hits_dealt=int(data.get("hits_dealt",    0)),
            hits_taken=int(data.get("hits_taken",    0)),
            steps=int(data.get("steps",         0)),
        )


@dataclass(slots=True)
class GameSession:
    """Полное состояние текущей игровой сессии.

    Содержит всё необходимое для сохранения и восстановления игры.
    Передаётся между ``GameEngine`` и ``presentation``- слоем.

    Attributes:
        hero (Character): Характеристики и оружие героя.
        hero_pos (Pos): Текущая позиция героя на карте.
        level (Level): Текущий уровень подземелья.
        backpack Backpack: Инвентарь героя.
        active_effects (list[ActiveEffect]): Список действующих временных эффектов.
        stats (RunStats): Накопленная статистика прохождения.
        current_level (int): Номер текущего уровня (1–21).
        sleep_turns (int): Ходов, на которые герой усыплён Змеем-магом
        game_over (bool): True если игра завершена (смерть или победа).
        victory (bool): True если игра завершена победой (21 уровень пройден).
        message (str): Последнее сообщение для статусной строки.
        rng_state (Any): Состояние ``random.Random`` для воспроизводимого восстановления 
                    сессии. Сохраняется через ``GameEngine.save_rng_state`` перед записью.
        last_visible (set[tuple[int, int]]): Кеш видимых клеток после последнего хода.
                                            Не сериализуется — пересчитывается при загрузке.
        difficulty_bias (int): Смещение сложности для автобаланса. Сохраняется в JSON. 
                               Положительное — сложнее, отрицательное — легче.
        level_damage_taken (int): Суммарный урон, полученный за текущий уровень.
                                  Не сериализуется. Сбрасывается при переходе на новый уровень. 
                                  Метрика для автобаланса — учитывает лечение и эликсиры 
                                  в отличие от простой разницы HP.
    """
    hero:           Character
    hero_pos:       Pos
    level:          Level
    backpack:       Backpack
    active_effects: list[ActiveEffect]
    stats:          RunStats
    current_level:  int
    sleep_turns:    int = 0
    game_over:      bool = False
    victory:        bool = False
    message:        str = ""
    rng_state:      Any = None
    last_visible:   set[tuple[int, int]] = field(
        default_factory=set, repr=False)
    difficulty_bias:  int = 0
    level_damage_taken: int = 0
    flash_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Статы героя"""
        return {
            "hero":            self.hero.to_dict(),
            "hero_pos":        [self.hero_pos.x, self.hero_pos.y],
            "level":           self.level.to_dict(),
            "backpack":        self.backpack.to_dict(),
            "active_effects":  [e.to_dict() for e in self.active_effects],
            "stats":           self.stats.to_dict(),
            "current_level":   self.current_level,
            "sleep_turns":     self.sleep_turns,
            "game_over":       self.game_over,
            "victory":         self.victory,
            "message":         self.message,
            "rng_state":       _rng_state_to_json(self.rng_state),
            "difficulty_bias": self.difficulty_bias,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "GameSession":
        """Распаковка статов героя"""
        raw_rng = data.get("rng_state")
        rng_state = _rng_state_from_json(
            raw_rng) if raw_rng is not None else None
        difficulty_bias = int(data.get("difficulty_bias", 0))

        return GameSession(
            hero=Character.from_dict(data["hero"]),
            hero_pos=Pos(int(data["hero_pos"][0]), int(data["hero_pos"][1])),
            level=Level.from_dict(data["level"]),
            backpack=Backpack.from_dict(data["backpack"]),
            active_effects=[
                ActiveEffect.from_dict(e) for e in data.get("active_effects", [])
            ],
            stats=RunStats.from_dict(data.get("stats", {})),
            current_level=int(data.get("current_level", 1)),
            sleep_turns=int(data.get("sleep_turns", 0)),
            difficulty_bias=difficulty_bias,
            game_over=bool(data.get("game_over", False)),
            victory=bool(data.get("victory", False)),
            message=str(data.get("message", "")),
            rng_state=rng_state
        )
