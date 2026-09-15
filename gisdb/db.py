r"""Подключение к базе данных проекта (PostgreSQL 18 + PostGIS).

Сейчас экземпляр развёрнут локально, отдельно от системного PostgreSQL:
каталог кластера `F:\GIS-DB\cluster`, порт 5433, пароли в `F:\GIS-DB\secrets`.
Ничего из этого не зашито намертво: адрес, порт, имя базы и корень хранилища
читаются из переменных окружения, чтобы переезд на сервер не требовал правок
кода.

| Переменная | По умолчанию |
|---|---|
| `GIS_DB_HOST` | `localhost` |
| `GIS_DB_PORT` | `5433` |
| `GIS_DB_NAME` | `gis_au_u` |
| `GIS_DB_ROOT` | `F:/GIS-DB` — каталог хранилища (растры, приёмник, секреты) |
| `GIS_DB_PASSWORD_<РОЛЬ>` | пароль роли; иначе берётся из файла в `secrets` |

Роли: `gis_read` — только чтение (анализ, QGIS), `gis_agent` — запись без
удаления (агент-сборщик), `gisadmin` — суперпользователь (миграции схемы).
"""

import os
from pathlib import Path

import psycopg
import sqlalchemy as sa

HOST: str = os.environ.get("GIS_DB_HOST", "localhost")
PORT: int = int(os.environ.get("GIS_DB_PORT", "5433"))
DBNAME: str = os.environ.get("GIS_DB_NAME", "gis_au_u")

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
ROOT: Path = Path(os.environ.get("GIS_DB_ROOT", "F:/GIS-DB"))

SECRETS_DIR: Path = ROOT / "secrets"
RASTER_DIR: Path = ROOT / "raster"      # архив растров (COG) вне базы
INCOMING_DIR: Path = ROOT / "incoming"  # скачанное агентом до паспортизации
BACKUP_DIR: Path = ROOT / "backup"

SCHEMAS: tuple[str, ...] = ("data", "meta", "runs", "kb", "bridge", "staging")


def password(role: str) -> str:
    """Пароль роли: из переменной окружения, иначе из файла секретов."""
    from_env = os.environ.get(f"GIS_DB_PASSWORD_{role.upper()}")
    if from_env:
        return from_env
    return (SECRETS_DIR / f"{role}.pw").read_text(encoding="utf-8").strip()


def url(role: str = "gis_read") -> str:
    """Строка подключения SQLAlchemy для роли."""
    return f"postgresql+psycopg://{role}:{password(role)}@{HOST}:{PORT}/{DBNAME}"


def engine(role: str = "gis_read", **kwargs) -> sa.Engine:
    """Движок SQLAlchemy; по умолчанию — роль только на чтение."""
    return sa.create_engine(url(role), **kwargs)


def connect(role: str = "gis_agent", **kwargs) -> psycopg.Connection:
    """Прямое подключение psycopg — нужно для COPY и явных транзакций."""
    return psycopg.connect(
        host=HOST, port=PORT, dbname=DBNAME,
        user=role, password=password(role), **kwargs,
    )


# ------------------------------------------------------- пути файлов вне базы
def store_ref(path: Path | str) -> str:
    """Ссылка на файл для записи в базу — относительная, без буквы диска.

    `проект:/data/processed/dataset_v6.parquet` — файл в репозитории,
    `хранилище:/raster/aster_b04.tif` — файл в каталоге данных.
    Абсолютные пути в базу не пишем: при переезде на сервер они все ложны.
    """
    p = Path(path)
    absolute = p if p.is_absolute() else (PROJECT_ROOT / p)
    for префикс, корень in (("хранилище", ROOT), ("проект", PROJECT_ROOT)):
        try:
            return f"{префикс}:/{absolute.resolve().relative_to(корень.resolve()).as_posix()}"
        except ValueError:
            continue
    return absolute.as_posix()


def store_path(ref: str) -> Path:
    """Обратное преобразование: ссылка из базы → путь на этой машине."""
    for префикс, корень in (("хранилище", ROOT), ("проект", PROJECT_ROOT)):
        if ref.startswith(f"{префикс}:/"):
            return корень / ref.split(":/", 1)[1]
    return Path(ref)
