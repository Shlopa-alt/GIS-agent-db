"""Установка базы с нуля одной командой: роли, база, расширения, схема.

Зачем. До сих пор установка была разложена по четырём местам: роли создавались
руками (и нигде не записаны), база — командой `createdb`, расширения — отдельным
`psql`, схема — накатчиком миграций. На рабочей машине это неважно: она
установлена месяц назад и работает. Важно это там, где базу ставят впервые —
у покупателя, на сервере организации, в непрерывной проверке, — и там же
выясняется, что половина шагов нигде не описана.

Что делает:

1. проверяет сервер — версию и наличие PostGIS, до того как что-то менять;
2. создаёт роли `gisadmin`, `gis_agent`, `gis_read` с паролями и кладёт пароли
   в хранилище секретов. Уже заведённые роли не трогает;
3. создаёт базу в UTF-8 с сортировкой ICU `ru-RU`;
4. ставит расширения и выдаёт `gisadmin` право писать в `spatial_ref_sys`;
5. накатывает миграции по учёту (`db/migrate.py`);
6. сверяет итог: миграции, своя система координат, роли, права.

**`gisadmin` не делается суперпользователем.** Раньше это выходило само собой:
расширения ставит только суперпользователь, а ставит их первая миграция — значит
и накатчику нужны были все права. Здесь расширения ставятся на шаге 4 от имени
суперпользователя кластера, и `gisadmin` остаётся тем, чем должен быть:
владельцем схемы с правом заводить роли и базы. Своей системе координат нужна
запись в `spatial_ref_sys` — это право выдаётся отдельно и явно, а не целым
суперпользователем ради одной таблицы.

Запуск от суперпользователя кластера (на свежем сервере это `postgres`); его
пароль берётся из `GIS_DB_SUPER_PASSWORD` или `PGPASSWORD`:

    python db/install.py --проверить          что будет сделано, ничего не менять
    python db/install.py
    python db/install.py --суперпользователь postgres --база gis_au_u
"""

from __future__ import annotations

import argparse
import os
import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # рядом лежит migrate

import psycopg
from psycopg import sql

from gisdb import db  # noqa: E402

#: Служебные роли и их назначение. Пароли этих ролей лежат в хранилище: ими
#: пользуются скрипты. Учётные записи людей заводит `db/add_reviewer.py`, и их
#: пароли в хранилище не попадают — см. его описание.
РОЛИ: tuple[tuple[str, str, str], ...] = (
    ("gisadmin", "CREATEDB CREATEROLE", "владелец схемы: миграции и права"),
    ("gis_agent", "", "агент: пишет черновики, не удаляет и не подтверждает"),
    ("gis_read", "", "только чтение: выгрузки, панель для читателя"),
)

РАСШИРЕНИЯ = ("postgis", "postgis_raster", "btree_gist", "pg_trgm")

#: Младше этой версии не ставим: в схеме есть то, чего в прежних нет.
МИНИМУМ = 16

АЛФАВИТ = string.ascii_letters + string.digits


def пароль() -> str:
    return "".join(secrets.choice(АЛФАВИТ) for _ in range(24))


def служебное(суперпользователь: str, пароль_супера: str, база: str = "postgres"):
    return psycopg.connect(host=db.HOST, port=db.PORT, dbname=база,
                           user=суперпользователь, password=пароль_супера,
                           autocommit=True)


# ------------------------------------------------------------------ 1. сервер
def проверить_сервер(conn) -> str:
    версия = conn.execute("SHOW server_version").fetchone()[0]
    старшая = int(версия.split(".")[0])
    if старшая < МИНИМУМ:
        raise SystemExit(
            f"сервер {версия}: нужен PostgreSQL {МИНИМУМ} или новее — "
            "схема пользуется тем, чего в прежних версиях нет"
        )
    доступно = {и for (и,) in conn.execute(
        "SELECT name FROM pg_available_extensions WHERE name = ANY(%s)",
        (list(РАСШИРЕНИЯ),))}
    нет = [и for и in РАСШИРЕНИЯ if и not in доступно]
    if нет:
        raise SystemExit(
            "на сервере нет расширений: " + ", ".join(нет)
            + ". PostGIS ставится отдельно от PostgreSQL — без него база не "
              "хранит ни геометрию, ни растры"
        )
    return версия


# -------------------------------------------------------------------- 2. роли
def завести_роли(conn, только_показать: bool) -> list[str]:
    """Создать отсутствующие роли с паролями; вернуть, что сделано."""
    сделано = []
    for имя, права, зачем in РОЛИ:
        есть = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s",
                            (имя,)).fetchone()
        файл = db.SECRETS_DIR / f"{имя}.pw"
        if есть:
            сделано.append(f"роль {имя}: уже есть, не тронута"
                           + ("" if файл.is_file() else f"; пароля в {файл.name} нет"))
            continue
        if только_показать:
            сделано.append(f"роль {имя}: будет создана ({зачем})")
            continue
        тайна = пароль()
        conn.execute(sql.SQL("CREATE ROLE {} WITH LOGIN " + права + " PASSWORD {}")
                     .format(sql.Identifier(имя), sql.Literal(тайна)))
        conn.execute(sql.SQL("COMMENT ON ROLE {} IS {}").format(
            sql.Identifier(имя), sql.Literal(зачем)))
        db.SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        файл.write_text(тайна, encoding="utf-8")
        сделано.append(f"роль {имя}: создана, пароль в {файл}")
    return сделано


# -------------------------------------------------------------------- 3. база
def завести_базу(conn, имя: str, только_показать: bool) -> str:
    есть = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s",
                        (имя,)).fetchone()
    if есть:
        return f"база {имя}: уже есть, не тронута"
    if только_показать:
        return f"база {имя}: будет создана (UTF-8, сортировка ICU ru-RU)"
    основа = sql.SQL("CREATE DATABASE {} OWNER gisadmin ENCODING 'UTF8' "
                     "TEMPLATE template0").format(sql.Identifier(имя))
    с_icu = sql.SQL("CREATE DATABASE {} OWNER gisadmin ENCODING 'UTF8' "
                    "LOCALE_PROVIDER icu ICU_LOCALE 'ru-RU' TEMPLATE template0"
                    ).format(sql.Identifier(имя))
    try:
        conn.execute(с_icu)
        return f"база {имя}: создана, сортировка ICU ru-RU"
    except psycopg.Error:
        # Сборка сервера может быть без ICU. Это не повод останавливать
        # установку: от сортировки зависит порядок списков, а не данные.
        conn.execute(основа)
        return (f"база {имя}: создана, сортировка по умолчанию — "
                "сервер собран без ICU, списки будут сортироваться иначе")


# -------------------------------------------------------------- 4. расширения
def поставить_расширения(conn, только_показать: bool) -> list[str]:
    """Расширения и право писать в spatial_ref_sys — вместо суперпользователя."""
    сделано = []
    for имя in РАСШИРЕНИЯ:
        есть = conn.execute("SELECT 1 FROM pg_extension WHERE extname = %s",
                            (имя,)).fetchone()
        if есть:
            сделано.append(f"расширение {имя}: уже есть")
        elif только_показать:
            сделано.append(f"расширение {имя}: будет поставлено")
        else:
            conn.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(
                sql.Identifier(имя)))
            сделано.append(f"расширение {имя}: поставлено")
    if только_показать:
        сделано.append("право записи в spatial_ref_sys: будет выдано gisadmin")
    else:
        # Миграция 004 пишет в эту таблицу свою систему координат. Таблица
        # принадлежит PostGIS, то есть суперпользователю, — отсюда и шло
        # требование суперпользователя для накатчика. Выдаём одно право.
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON spatial_ref_sys TO gisadmin")
        сделано.append("право записи в spatial_ref_sys: выдано gisadmin")
    return сделано


# ----------------------------------------------------------------- 6. сверка
def сверить(conn) -> list[tuple[str, str, bool]]:
    схема = Path(__file__).resolve().parent / "sql"
    файлов = len(list(схема.glob("*.sql")))
    применено = conn.execute("SELECT count(*) FROM meta.migration").fetchone()[0]
    срид = conn.execute(
        "SELECT count(*) FROM spatial_ref_sys WHERE srid = 990018").fetchone()[0]
    роли = {и for (и,) in conn.execute(
        "SELECT rolname FROM pg_roles WHERE rolname LIKE 'gis%'")}
    нужные = {"gisadmin", "gis_agent", "gis_read", "gis_human", "gis_confirmer"}
    таблиц = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = ANY(%s)",
        (list(db.SCHEMAS),)).fetchone()[0]
    агент_пишет = conn.execute(
        """SELECT count(*) FROM information_schema.table_privileges
            WHERE grantee = 'gis_agent' AND privilege_type = 'INSERT'"""
    ).fetchone()[0]
    return [
        ("миграции", f"{применено} из {файлов}", применено == файлов),
        ("таблиц в схемах", str(таблиц), таблиц > 0),
        ("своя система координат 990018", "есть" if срид else "НЕТ", bool(срид)),
        ("роли", ", ".join(sorted(нужные & роли)) or "нет",
         not (нужные - роли)),
        ("агент вправе писать", f"{агент_пишет} таблиц", агент_пишет > 0),
    ]


def main(аргументы: list[str] | None = None) -> int:
    разбор = argparse.ArgumentParser(description="Установка базы с нуля")
    разбор.add_argument("--суперпользователь", default="postgres",
                        help="роль кластера, от которой ставим (умолчание postgres)")
    разбор.add_argument("--база", default=db.DBNAME, help=f"имя базы (умолчание {db.DBNAME})")
    разбор.add_argument("--проверить", action="store_true",
                        help="показать, что будет сделано, и ничего не менять")
    ключи = разбор.parse_args(аргументы)

    тайна = os.environ.get("GIS_DB_SUPER_PASSWORD") or os.environ.get("PGPASSWORD")
    if not тайна:
        try:
            тайна = db.password(ключи.суперпользователь)
        except RuntimeError:
            raise SystemExit(
                f"не найден пароль суперпользователя {ключи.суперпользователь}: "
                "задайте его переменной GIS_DB_SUPER_PASSWORD"
            ) from None

    print(f"сервер {db.HOST}:{db.PORT}, база {ключи.база}, "
          f"хранилище {db.ROOT}")
    if ключи.проверить:
        print("режим проверки: ничего не меняется\n")

    with служебное(ключи.суперпользователь, тайна) as служба:
        версия = проверить_сервер(служба)
        print(f"сервер: PostgreSQL {версия}, расширения на месте\n")
        for строка in завести_роли(служба, ключи.проверить):
            print(" ", строка)
        print(" ", завести_базу(служба, ключи.база, ключи.проверить))

    if ключи.проверить:
        print("\nдальше: расширения, накат миграций, сверка — "
              "запустите без --проверить")
        return 0

    with служебное(ключи.суперпользователь, тайна, ключи.база) as в_базе:
        for строка in поставить_расширения(в_базе, False):
            print(" ", строка)

    print("\nнакат схемы:")
    # Имя базы может отличаться от умолчания (--база). Накатчик берёт его из
    # того же модуля `db`, поэтому достаточно поправить там: у обоих это один
    # и тот же объект модуля, а не копия.
    db.DBNAME = ключи.база
    import migrate                                  # лежит рядом, в db/

    код = migrate.main([])
    if код:
        print("схема накатана не до конца — сверку не делаем", file=sys.stderr)
        return код

    print("\nсверка:")
    try:
        conn = db.connect("gisadmin")
    except Exception as беда:                                   # noqa: BLE001
        print(f"  схема накатана, но сверить не удалось: {беда}", file=sys.stderr)
        print("  пароль gisadmin не в хранилище? "
              "python db/set_password.py gisadmin", file=sys.stderr)
        return 1
    with conn:
        плохо = 0
        for что, значение, ладно in сверить(conn):
            плохо += not ладно
            print(f"  {что:32} {значение:28} {'да' if ладно else 'НЕТ'}")

    print()
    if плохо:
        print("установка завершена с замечаниями — разбираться по строкам «НЕТ»")
        return 1
    print("база установлена. Дальше:")
    print("  python db/set_password.py gis_human     пароль роли панели")
    print("  python db/add_reviewer.py --кто ivanov  учётная запись человека")
    print("  python scripts/db_load_datasets.py      наполнение данными проекта")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
