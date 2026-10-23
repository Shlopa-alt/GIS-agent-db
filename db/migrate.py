"""Накат миграций схемы по учёту: что применено, что ждёт, что изменилось.

Раньше файлы `db/sql/*.sql` накатывались руками по одному, и база не знала,
какие из них прошли. Это не теория: с 30.09 по 22.10 база жила без миграций 011
и 012, и выяснилось это случайно — выборкой по списку таблиц. Установку в таком
виде нельзя ни воспроизвести на другой машине, ни обновить у того, кому систему
передадут.

    python db/migrate.py --состояние            что применено, что ждёт
    python db/migrate.py                        применить недостающие
    python db/migrate.py --отметить-применёнными  зарегистрировать без исполнения

Последняя команда нужна один раз на уже работающей базе: схема в ней есть, а
учёта нет, и повторное исполнение файлов было бы в лучшем случае бесполезным.
После неё база и код сходятся, дальше всё идёт обычным путём.

**Контрольная сумма.** У применённой миграции хранится sha256 файла. Если файл
правят задним числом, базы, накатанные до и после правки, различаются при
одинаковом списке применённого — сверка это ловит и отказывается работать,
называя файл. Исправлять схему нужно новой миграцией, а не правкой старой.

Роль — `gisadmin`: миграции меняют схему, и это единственное, что делается
суперпользователем.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gisdb import db  # noqa: E402

ПАПКА = Path(__file__).resolve().parent / "sql"

#: Миграция, заводящая сам учёт: её применяет этот скрипт, пока таблицы ещё нет.
УЧЁТ = "014_migrations.sql"

ЕСТЬ_ТАБЛИЦА = """
SELECT EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'meta' AND table_name = 'migration')
"""


def файлы() -> list[Path]:
    """Миграции по порядку имён: номер в имени и задаёт очередь."""
    return sorted(ПАПКА.glob("*.sql"))


def отпечаток(путь: Path) -> str:
    return hashlib.sha256(путь.read_bytes()).hexdigest()


def применённые(conn) -> dict[str, str]:
    строки = conn.execute("SELECT filename, checksum FROM meta.migration").fetchall()
    return {имя: сумма for имя, сумма in строки}


def завести_учёт(conn) -> bool:
    """Создать таблицу учёта, если её нет. Возвращает, пришлось ли заводить.

    Схема `meta` создаётся здесь же. Учёт живёт в ней, а появляется она только
    в первой миграции — на живой базе это незаметно, а на пустой накат падал на
    первом же шаге: «schema meta does not exist». Сам файл учёта не трогаем, его
    отпечаток уже записан в базе, и правка задним числом остановила бы накат.
    """
    if conn.execute(ЕСТЬ_ТАБЛИЦА).fetchone()[0]:
        return False
    путь = ПАПКА / УЧЁТ
    conn.execute("CREATE SCHEMA IF NOT EXISTS meta")
    conn.execute(путь.read_text(encoding="utf-8"))
    conn.execute(
        """INSERT INTO meta.migration (filename, checksum, note)
           VALUES (%s, %s, 'учёт миграций заведён этим же файлом')
           ON CONFLICT (filename) DO NOTHING""",
        (УЧЁТ, отпечаток(путь)),
    )
    conn.commit()
    return True


def расхождения(conn) -> list[str]:
    """Применённые миграции, файл которых с тех пор изменился."""
    было = применённые(conn)
    плохие = []
    for путь in файлы():
        сумма = было.get(путь.name)
        if сумма and сумма != отпечаток(путь):
            плохие.append(путь.name)
    return плохие


def состояние(conn) -> int:
    if not conn.execute(ЕСТЬ_ТАБЛИЦА).fetchone()[0]:
        # Показ состояния ничего не меняет в базе — в том числе не заводит учёт.
        print(f"учёта миграций в базе нет; файлов схемы: {len(файлы())}")
        print("завести и зарегистрировать уже накатанное: "
              "python db/migrate.py --отметить-применёнными")
        return 0
    было = применённые(conn)
    ждут = [п.name for п in файлы() if п.name not in было]
    print(f"применено: {len(было)}, ожидает: {len(ждут)}")
    for имя, сумма in sorted(было.items()):
        print(f"  применена  {имя}  {сумма[:12]}")
    for имя in ждут:
        print(f"  ОЖИДАЕТ    {имя}")
    плохие = расхождения(conn)
    for имя in плохие:
        print(f"  ИЗМЕНЁН ПОСЛЕ ПРИМЕНЕНИЯ: {имя}", file=sys.stderr)
    return 1 if плохие else 0


def отметить(conn) -> int:
    """Зарегистрировать все файлы как применённые, не исполняя их."""
    было = применённые(conn)
    новые = [п for п in файлы() if п.name not in было]
    for путь in новые:
        conn.execute(
            """INSERT INTO meta.migration (filename, checksum, note)
               VALUES (%s, %s, 'отмечена применённой задним числом')""",
            (путь.name, отпечаток(путь)),
        )
    conn.commit()
    print(f"отмечено применёнными без исполнения: {len(новые)}")
    for путь in новые:
        print(f"  {путь.name}")
    return 0


def накатить(conn) -> int:
    """Применить недостающие миграции по порядку, каждую своей транзакцией."""
    плохие = расхождения(conn)
    if плохие:
        print("миграции изменены после применения: " + ", ".join(плохие) +
              ". Схему правят новой миграцией, а не правкой старой", file=sys.stderr)
        return 1

    было = применённые(conn)
    ждут = [п for п in файлы() if п.name not in было]
    if not ждут:
        print("применять нечего: база в согласии с кодом")
        return 0

    for путь in ждут:
        начало = time.monotonic()
        print(f"применяю {путь.name} …", end="", flush=True)
        try:
            conn.execute(путь.read_text(encoding="utf-8"))
            conn.execute(
                """INSERT INTO meta.migration (filename, checksum, seconds)
                   VALUES (%s, %s, %s)""",
                (путь.name, отпечаток(путь), round(time.monotonic() - начало, 3)),
            )
            conn.commit()
        except Exception as сбой:  # noqa: BLE001 — причина важнее типа
            conn.rollback()
            print(f" сбой\n{путь.name}: {сбой}", file=sys.stderr)
            print("остальные миграции не применялись: порядок важен", file=sys.stderr)
            return 1
        print(f" готово за {time.monotonic() - начало:.1f} с")
    return 0


def main(аргументы: list[str] | None = None) -> int:
    разбор = argparse.ArgumentParser(description="Миграции схемы по учёту")
    разбор.add_argument("--состояние", action="store_true", help="показать и выйти")
    разбор.add_argument("--отметить-применёнными", dest="отметить", action="store_true",
                        help="зарегистрировать файлы без исполнения (разовая операция)")
    ключи = разбор.parse_args(аргументы)

    with db.connect("gisadmin") as conn:
        conn.autocommit = False
        if ключи.состояние:                 # ничего не меняем, даже учёт не заводим
            return состояние(conn)
        if завести_учёт(conn):
            print(f"учёт миграций заведён: {УЧЁТ}")
        if ключи.отметить:
            return отметить(conn)
        return накатить(conn)


if __name__ == "__main__":
    raise SystemExit(main())
