"""Ядро базы не требует репозитория прогноза рядом.

Зачем это проверять. База продаётся как система, а не как приложение к
исследовательскому репозиторию. Пока ядро импортировало `src.feature_dict`,
база, отданная другому, не открывала даже карточку признака: панель падала на
импорте модуля, которого у него нет. Такую зависимость легко вернуть одной
строкой `from src...` — и заметно это станет только у покупателя.

Живая база здесь не нужна: проверяется разбор кода, а не его работа.
"""

import ast
import sys
from pathlib import Path

import pytest

КОРЕНЬ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕНЬ))

#: Загрузчикам проект нужен по делу: они переносят в базу посчитанное в нём.
ЗАГРУЗЧИКИ = {
    "db_load_dictionary.py",   # снимок словаря признаков
    "db_fill_checks.py",       # важность и переносимость из таблиц расчётов
    "db_register_layers.py",   # сами слои лежат в проекте
}


def модули_ядра() -> list[Path]:
    return [п for п in (КОРЕНЬ / "gisdb").rglob("*.py")
            if "__pycache__" not in п.parts]


def _импорты(путь: Path) -> set[str]:
    дерево = ast.parse(путь.read_text(encoding="utf-8"), filename=str(путь))
    имена: set[str] = set()
    for узел in ast.walk(дерево):
        if isinstance(узел, ast.Import):
            имена |= {и.name.split(".")[0] for и in узел.names}
        elif isinstance(узел, ast.ImportFrom) and узел.module and узел.level == 0:
            имена.add(узел.module.split(".")[0])
    return имена


@pytest.mark.parametrize("путь", модули_ядра(), ids=lambda п: п.name)
def test_ядро_не_импортирует_проект(путь):
    """`gisdb/` читает базу, а не чужой репозиторий. Исключений нет."""
    assert "src" not in _импорты(путь), (
        f"{путь.relative_to(КОРЕНЬ)} импортирует пакет проекта. Данные проекта "
        "попадают в базу загрузчиками из scripts/, ядро читает только базу"
    )


def _из_пакета(имя: str, пакет: str) -> bool:
    return имя == пакет or имя.startswith(пакет + ".")


def test_ядро_живёт_без_каталога_проекта(monkeypatch):
    """Полная проверка: импорт ядра при заведомо отсутствующем проекте.

    Разбор кода ловит `import src`, но не ловит обращение к файлу проекта на
    месте. Поэтому ядро импортируется заново с заведомо неверным путём.
    """
    monkeypatch.setenv("GIS_PROJECT_ROOT", str(КОРЕНЬ / "нет-такого-каталога"))
    for имя in [м for м in list(sys.modules) if _из_пакета(м, "gisdb")]:
        sys.modules.pop(имя, None)
    import gisdb.feature_facts  # noqa: F401
    import gisdb.panel  # noqa: F401
    assert not [м for м in sys.modules if _из_пакета(м, "src")]


def test_проект_нужен_только_загрузчикам():
    """Кто ещё обращается к проекту — тот должен быть в списке загрузчиков."""
    с_проектом = {
        п.name for п in (КОРЕНЬ / "scripts").glob("*.py")
        if "src" in _импорты(п) or "gisdb.project" in п.read_text(encoding="utf-8")
    }
    лишние = с_проектом - ЗАГРУЗЧИКИ
    assert not лишние, (
        f"эти скрипты требуют репозиторий прогноза, а в список загрузчиков не "
        f"внесены: {sorted(лишние)}. Либо читайте базу, либо внесите осознанно"
    )
