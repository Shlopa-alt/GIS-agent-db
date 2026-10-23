"""Учётная запись проверяющего: своя роль с правом подписи или без него.

Зачем отдельные записи, а не один общий `gis_human`. Подпись под паспортом —
главное, что хранит база: 240 паспортов подтверждены человеком, и когда-нибудь
придётся отвечать на вопрос «кто именно подтвердил». Пока все входят под одной
ролью, ответ один для всех, и правило «отклонение с причиной» подписано тем же
именем, что и подтверждение. Своя роль у каждого разбирает это по людям.

Право вынести вердикт даёт членство в `gis_confirmer` — его проверяет триггер
`meta.guard_confirmation`, а не панель. Поэтому читателю достаточно не выдать
членство: он увидит очередь и карточки, но его решение отклонит база.

    python db/add_reviewer.py --кто ivanov                   подтверждающий
    python db/add_reviewer.py --кто petrov --только-чтение    читатель
    python db/add_reviewer.py --кто ivanov --пароль свой-пароль
    python db/add_reviewer.py --список                       кто заведён

Имя записи — латиницей и в нижнем регистре: оно уходит в `confirmed_by`, в
строку подключения и в журнал, а кириллица в имени роли живёт только в кавычках
и ломается в половине мест. Человеческое имя пишется в примечание роли.

Пароль по умолчанию генерируется и печатается один раз — его надо передать
человеку. В хранилище секретов он **не** кладётся: пароль проверяющего знает
только он сам, иначе подпись опять ничья. Этим записи и отличаются от
служебных ролей (`gis_agent`, `gis_read`), чьи пароли лежат в `secrets`, потому
что ими пользуются скрипты, а не люди.

Запускается от `gisadmin`: это изменение прав, его делает человек.
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from pathlib import Path

from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gisdb import db  # noqa: E402

АЛФАВИТ = string.ascii_letters + string.digits
СЛУЖЕБНЫЕ = ("gisadmin", "gis_agent", "gis_read", "gis_human", "gis_confirmer")


def сгенерировать(длина: int = 16) -> str:
    return "".join(secrets.choice(АЛФАВИТ) for _ in range(длина))


def проверить_имя(имя: str) -> str:
    имя = имя.strip().lower()
    если_плохо = (
        not имя
        or not имя[0].isalpha()
        or not all(с.isalnum() or с == "_" for с in имя)
        or not имя.isascii()
    )
    if если_плохо:
        raise SystemExit(
            f"имя учётной записи «{имя}» не годится: только латиница, цифры и "
            "подчёркивание, начинается с буквы. Человеческое имя пойдёт в "
            "примечание (--подпись)"
        )
    if имя in СЛУЖЕБНЫЕ:
        raise SystemExit(f"{имя} — служебная роль базы, не учётная запись человека")
    return имя


def список(conn) -> None:
    строки = conn.execute(
        """SELECT r.rolname,
                  pg_has_role(r.rolname, 'gis_confirmer', 'MEMBER') AS подпись,
                  r.rolcanlogin,
                  shobj_description(r.oid, 'pg_authid')
             FROM pg_roles r
            WHERE r.rolcanlogin AND r.rolname NOT IN ('gisadmin', 'gis_agent', 'gis_read')
            ORDER BY r.rolname"""
    ).fetchall()
    if not строки:
        print("учётных записей проверяющих нет")
        return
    print(f"{'запись':20} {'подпись':9} {'вход':5} примечание")
    for имя, подпись, вход, примечание in строки:
        print(f"{имя:20} {'да' if подпись else 'нет':9} "
              f"{'да' if вход else 'нет':5} {примечание or ''}")


def завести(conn, имя: str, пароль: str, может_решать: bool, подпись: str | None) -> bool:
    """Создать или обновить роль. Возвращает, была ли она новой."""
    есть = conn.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", (имя,)
    ).fetchone() is not None
    # Имя роли и пароль в DDL параметрами не подставляются — экранируем сами.
    запрос = "ALTER ROLE {} WITH LOGIN PASSWORD {}" if есть else \
             "CREATE ROLE {} WITH LOGIN PASSWORD {}"
    conn.execute(sql.SQL(запрос).format(sql.Identifier(имя), sql.Literal(пароль)))
    conn.execute(sql.SQL("GRANT gis_human TO {}").format(sql.Identifier(имя)))
    if может_решать:
        conn.execute(sql.SQL("GRANT gis_confirmer TO {}").format(sql.Identifier(имя)))
    else:
        # Членство снимается явно: запись могли заводить подтверждающим.
        conn.execute(sql.SQL("REVOKE gis_confirmer FROM {}").format(sql.Identifier(имя)))
    if подпись:
        conn.execute(sql.SQL("COMMENT ON ROLE {} IS {}").format(
            sql.Identifier(имя), sql.Literal(подпись)))
    return not есть


def main(аргументы: list[str] | None = None) -> int:
    разбор = argparse.ArgumentParser(description="Учётная запись проверяющего")
    разбор.add_argument("--кто", metavar="ИМЯ", help="имя записи латиницей")
    разбор.add_argument("--пароль", help="задать свой пароль вместо случайного")
    разбор.add_argument("--подпись", help="человеческое имя в примечание роли")
    разбор.add_argument("--только-чтение", dest="читатель", action="store_true",
                        help="без права выносить вердикт")
    разбор.add_argument("--список", dest="показать", action="store_true",
                        help="показать заведённые записи")
    ключи = разбор.parse_args(аргументы)

    if not ключи.кто and not ключи.показать:
        разбор.error("укажите --кто или --список")

    with db.connect("gisadmin", autocommit=True) as conn:
        if ключи.показать and not ключи.кто:
            список(conn)
            return 0
        имя = проверить_имя(ключи.кто)
        пароль = ключи.пароль or сгенерировать()
        новая = завести(conn, имя, пароль, not ключи.читатель, ключи.подпись)
        print(f"учётная запись {имя}: {'создана' if новая else 'обновлена'}; "
              f"{'может выносить вердикт' if not ключи.читатель else 'только чтение'}")
        if not ключи.пароль:
            print(f"пароль (показан один раз, в хранилище не записан): {пароль}")
        print("вход в панель: http://127.0.0.1:8765/вход")
        if ключи.показать:
            print()
            список(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
