"""Модуль главного процесса"""
from datalayer.storage import JsonDataLayer
from domain.engine import GameEngine
from presentation.curses_app import CursesApp


def main() -> None:
    """Главный процесс игры"""
    engine = GameEngine()
    data_layer = JsonDataLayer("savegame.json")
    app = CursesApp(engine, data_layer)
    app.run()


if __name__ == "__main__":
    main()
