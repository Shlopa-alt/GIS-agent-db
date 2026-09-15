"""Проверка восстановления: копия считается копией только после обратного пути.

Резервная копия, из которой ни разу не восстанавливали базу, — это файл
неизвестного назначения. Скрипт проходит путь целиком: берёт дамп, поднимает из
него отдельную базу, сверяет количества строк с рабочей и убирает за собой.

    python db/restore_check.py                — по последнему дампу в хранилище
    python db/restore_check.py --свежий       — сперва сделать новый дамп
    python db/restore_check.py --оставить     — не удалять проверочную базу

Проверочная база называется `<база>_restore_check` и удаляется в конце: имя
проверяется отдельно, чтобы скрипт физически не мог снести рабочую.
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg

from gisdb import db

BIN = Path(os.environ.get("GIS_DB_BIN", db.ROOT / "pg18" / "bin"))
# Имя латиницей: имя базы уходит в psql через командную строку, а там
# кириллица теряется в кодировке консоли Windows.
ПРОВЕРОЧНАЯ = f"{db.DBNAME}_restore_check"

# Что сверяем: таблицы, по которым видно и данные, и паспорта, и журналы.
ТАБЛИЦЫ = (
    "data.territory", "data.grid", "data.cell", "data.source_layer",
    "data.feature", "data.feature_value",
    "meta.feature_passport", "meta.layer_passport", "meta.license",
    "meta.operation", "meta.derivation", "meta.gap", "meta.review",
    "runs.run", "runs.llm_call", "runs.metric", "bridge.feature_concept",
)


def окружение() -> dict:
    # PGCLIENTENCODING — чтобы сообщения сервера приходили в utf-8, а не в
    # кодировке консоли, которую потом не разобрать.
    return {**os.environ, "PGPASSWORD": db.password("gisadmin"),
            "PGCLIENTENCODING": "UTF8"}


def расшифровать(поток: bytes) -> str:
    """Сообщения psql приходят в кодировке консоли, а не в utf-8.

    На русской Windows это cp866, при другой настройке — cp1251; угадывать
    нечем, поэтому пробуем по очереди, а последней ставим замену негодных
    байтов: сообщение об ошибке важнее его красоты.
    """
    for кодировка in ("utf-8", "cp1251", "cp866"):
        try:
            return поток.decode(кодировка)
        except UnicodeDecodeError:
            continue
    return поток.decode("utf-8", "replace")


def запуск(программа: str, *аргументы: str) -> tuple[int, str]:
    команда = [str(BIN / программа), "-h", db.HOST, "-p", str(db.PORT),
               "-U", "gisadmin", *аргументы]
    готово = subprocess.run(команда, env=окружение(), capture_output=True)
    return готово.returncode, расшифровать(готово.stderr)


def обслуживание() -> psycopg.Connection:
    """Подключение к служебной базе: создать и удалить базу изнутри неё нельзя."""
    return psycopg.connect(host=db.HOST, port=db.PORT, dbname="postgres",
                           user="gisadmin", password=db.password("gisadmin"),
                           autocommit=True)


def убрать(conn: psycopg.Connection) -> None:
    assert ПРОВЕРОЧНАЯ.endswith("_restore_check") and ПРОВЕРОЧНАЯ != db.DBNAME
    conn.execute(f'DROP DATABASE IF EXISTS "{ПРОВЕРОЧНАЯ}" WITH (FORCE)')


def соединение(dbname: str) -> psycopg.Connection:
    return psycopg.connect(host=db.HOST, port=db.PORT, dbname=dbname,
                           user="gisadmin", password=db.password("gisadmin"))


def количества(conn: psycopg.Connection) -> dict[str, int]:
    запрос = " UNION ALL ".join(
        f"SELECT '{т}' AS таблица, count(*) FROM {т}" for т in ТАБЛИЦЫ
    )
    return dict(conn.execute(запрос).fetchall())


def одиночный_ключ(conn: psycopg.Connection, таблица: str) -> str | None:
    """Имя первичного ключа, если он единственный и числовой, иначе None."""
    столбцы = conn.execute(
        """SELECT a.attname, t.typcategory
             FROM pg_index i
             JOIN pg_attribute a ON a.attrelid = i.indrelid
                                AND a.attnum = ANY(i.indkey)
             JOIN pg_type t ON t.oid = a.atttypid
            WHERE i.indrelid = %s::regclass AND i.indisprimary""", (таблица,)
    ).fetchall()
    if len(столбцы) != 1 or столбцы[0][1] != "N":
        return None
    return столбцы[0][0]


def прирост(рабочая: psycopg.Connection, копия: psycopg.Connection,
            таблица: str, в_копии: int) -> int | None:
    """Отстала ли копия от рабочей базы, а не потеряла строки.

    Дамп снимается с живой базы: пока работает агент, журналы и выводки
    прирастают, и копия отстаёт на эти строки. Это не потеря, но разницу надо
    доказать — все строки копии обязаны найтись в рабочей базе, а лишние в ней
    иметь номера больше последнего номера в копии. None — доказать нечем
    (составной или нечисловой ключ, пустая таблица) или строк действительно не
    хватает.
    """
    ключ = одиночный_ключ(рабочая, таблица)
    if ключ is None:
        return None
    граница = копия.execute(f"SELECT max({ключ}) FROM {таблица}").fetchone()[0]
    if граница is None:
        return None
    старых = рабочая.execute(
        f"SELECT count(*) FROM {таблица} WHERE {ключ} <= %s", (граница,)
    ).fetchone()[0]
    return None if старых != в_копии else старых


def последний_дамп() -> Path:
    дампы = sorted(db.BACKUP_DIR.glob(f"{db.DBNAME}_*.dump"))
    if not дампы:
        raise SystemExit(f"в {db.BACKUP_DIR} нет дампов: сперва python db/backup.py")
    return дампы[-1]


def main(argv: list[str]) -> int:
    if "--свежий" in argv:
        import backup                                   # тот же каталог
        backup.main()

    дамп = последний_дамп()
    print(f"дамп: {дамп.name}, {дамп.stat().st_size / 1024 / 1024:.0f} МБ", flush=True)

    with обслуживание() as служебная:
        убрать(служебная)
        служебная.execute(f'CREATE DATABASE "{ПРОВЕРОЧНАЯ}" OWNER gisadmin')
    print(f"поднимаю {ПРОВЕРОЧНАЯ}", flush=True)

    try:
        for шаг, аргументы in (
            ("расширения", ("-d", ПРОВЕРОЧНАЯ, "-c",
                            "CREATE EXTENSION postgis; CREATE EXTENSION postgis_raster;")),
            ("система координат", ("-d", ПРОВЕРОЧНАЯ, "-v", "ON_ERROR_STOP=1",
                                   "-f", str(Path(__file__).parent / "sql" / "004_srid.sql"))),
        ):
            код, жалобы = запуск("psql", *аргументы)
            if код:
                print(f"{шаг}: сбой\n{жалобы[:600]}")
                return 1
            print(f"{шаг}: готово", flush=True)

        код, жалобы = запуск("pg_restore", "-d", ПРОВЕРОЧНАЯ, "--no-owner",
                             "-j", "4", str(дамп))
        if код:
            # pg_restore ворчит на права ролей — это не потеря данных, но
            # молчать об этом нельзя: пусть остаётся в выводе.
            print(f"pg_restore вернул {код}; первые замечания:\n"
                  + "\n".join(жалобы.splitlines()[:5]), flush=True)
        print("восстановление завершено, сверяю количества", flush=True)

        рабочая, копия = соединение(db.DBNAME), соединение(ПРОВЕРОЧНАЯ)
        было, стало = количества(рабочая), количества(копия)
        расхождений = наросло = 0
        print(f"\n{'таблица':28} {'рабочая':>12} {'копия':>12}  сходится")
        for т in ТАБЛИЦЫ:
            if было[т] == стало[т]:
                вывод = "да"
            elif стало[т] < было[т] and прирост(рабочая, копия, т, стало[т]):
                вывод = f"копия старше на {было[т] - стало[т]}"
                наросло += 1
            else:
                вывод = "НЕТ"
                расхождений += 1
            print(f"{т:28} {было[т]:12d} {стало[т]:12d}  {вывод}")
        рабочая.close()
        копия.close()
        if наросло:
            print(f"таблиц приросло после снимка: {наросло} — все строки копии "
                  "на месте, новое идёт следом за последним номером в копии")

        with psycopg.connect(host=db.HOST, port=db.PORT, dbname=ПРОВЕРОЧНАЯ,
                             user="gisadmin", password=db.password("gisadmin")) as conn:
            своя_срид = conn.execute(
                "SELECT count(*) FROM spatial_ref_sys WHERE srid = 990018"
            ).fetchone()[0]
        print(f"\nсвоя система координат 990018 в копии: {'есть' if своя_срид else 'НЕТ'}")

        роли = sorted(db.BACKUP_DIR.glob("roles_*.sql"))
        нужные = {"gisadmin", "gis_agent", "gis_read", "gis_human"}
        текст = роли[-1].read_text(encoding="utf-8") if роли else ""
        нет_ролей = sorted(р for р in нужные if f"CREATE ROLE {р}" not in текст)
        print(f"роли в {роли[-1].name if роли else 'файле ролей'}: "
              + ("все на месте" if not нет_ролей else "НЕ ХВАТАЕТ " + ", ".join(нет_ролей)))

        плохо = расхождений or not своя_срид or нет_ролей
        print("\nитог: " + ("копия воспроизводит базу" if not плохо
                            else "копия НЕ воспроизводит базу — разбираться"))
        return 1 if плохо else 0
    finally:
        if "--оставить" in argv:
            print(f"проверочная база {ПРОВЕРОЧНАЯ} оставлена")
        else:
            with обслуживание() as служебная:
                убрать(служебная)
            print(f"проверочная база {ПРОВЕРОЧНАЯ} удалена")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
