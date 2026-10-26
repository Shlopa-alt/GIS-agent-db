"""Поиск каталога хранилища и ссылки на файлы вне базы.

Проверяется то, на чём уже обожглись: жёсткое умолчание `F:/GIS-DB` пережило
переезд на другую машину, и каждая команда падала на чтении файла пароля, не
объясняя причины. Живая база здесь не нужна — только файлы и переменные.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gisdb import db  # noqa: E402


def _хранилище(корень: Path) -> Path:
    """Похожий на настоящий каталог хранилища: опознаётся по секретам."""
    (корень / "secrets").mkdir(parents=True)
    (корень / "secrets" / "gis_read.pw").write_text("тайна", encoding="utf-8")
    return корень


def test_переменная_сильнее_известных_мест(tmp_path, monkeypatch):
    свой = _хранилище(tmp_path / "своё")
    monkeypatch.setenv("GIS_DB_ROOT", str(свой))
    assert db._корень() == свой


def test_без_переменной_берётся_каталог_с_секретами(tmp_path, monkeypatch):
    пустой, настоящий = tmp_path / "пусто", _хранилище(tmp_path / "есть")
    monkeypatch.delenv("GIS_DB_ROOT", raising=False)
    monkeypatch.setattr(db, "ВОЗМОЖНЫЕ_КОРНИ", (пустой, настоящий))
    assert db._корень() == настоящий, "хранилище опознаётся по секретам, а не по имени"


def test_если_не_нашлось_ошибка_называет_где_искали(tmp_path, monkeypatch):
    monkeypatch.delenv("GIS_DB_ROOT", raising=False)
    monkeypatch.setattr(db, "SECRETS_DIR", tmp_path / "нет" / "secrets")
    monkeypatch.setattr(db, "ВОЗМОЖНЫЕ_КОРНИ", (tmp_path / "нет",))
    with pytest.raises(RuntimeError) as сбой:
        db.password("gis_read")
    текст = str(сбой.value)
    assert "GIS_DB_ROOT" in текст and str(tmp_path / "нет") in текст


def test_пароль_из_переменной_окружения_файл_не_нужен(monkeypatch):
    monkeypatch.setenv("GIS_DB_PASSWORD_GIS_READ", "из-переменной")
    assert db.password("gis_read") == "из-переменной"


def test_ссылка_на_файл_без_буквы_диска():
    """Три корня, и каждый свой. Пока «проект» значил и репозиторий прогноза, и
    репозиторий базы, слои ГИС Интегро записывались как `проект:/data/...`, а
    читались из каталога базы, где данных нет: осмотр сборщика насчитал 33
    пропавших слоя при 33 лежащих на месте.
    """
    свой = db.store_ref(db.PROJECT_ROOT / "db" / "migrate.py")
    assert свой == "база:/db/migrate.py"
    assert db.store_path(свой) == db.PROJECT_ROOT / "db/migrate.py"

    данные = db.store_ref(db.КОРЕНЬ_ПРОЕКТА / "data" / "processed" / "dataset_v6.parquet")
    assert данные == "проект:/data/processed/dataset_v6.parquet"
    assert db.store_path(данные) == db.КОРЕНЬ_ПРОЕКТА / "data/processed/dataset_v6.parquet"

    assert db.store_ref(db.ROOT / "backup" / "x.dump") == "хранилище:/backup/x.dump"


def test_утилита_находится_по_образцу_а_не_по_версии(tmp_path, monkeypatch):
    """`pg18` в пути — та же мина, что `F:/GIS-DB`: номер версии меняется."""
    bin_ = tmp_path / "pg19" / "bin"
    bin_.mkdir(parents=True)
    (bin_ / "pg_dump.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(db, "ROOT", tmp_path)
    assert db.утилита("pg_dump") == bin_ / "pg_dump.exe"


def test_утилиты_нет_в_хранилище_берётся_из_пути(tmp_path, monkeypatch):
    """На сервере организации Postgres ставится системно — имени достаточно."""
    monkeypatch.setattr(db, "ROOT", tmp_path)
    assert db.утилита("pg_dump") == Path("pg_dump")
