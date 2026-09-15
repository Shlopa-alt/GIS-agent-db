"""Пароль роли: сгенерировать, положить в хранилище секретов, задать в базе.

Пароли в репозиторий не попадают — они живут только в `F:\\GIS-DB\\secrets`,
откуда их читает `gisdb/db.password`. Скрипт нужен при заведении новой роли
(например `gis_human` для панели подтверждений) и при смене пароля.

Запуск: python db/set_password.py gis_human
"""

import secrets
import string
import sys
from pathlib import Path

from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gisdb import db

АЛФАВИТ = string.ascii_letters + string.digits


def сгенерировать(длина: int = 24) -> str:
    return "".join(secrets.choice(АЛФАВИТ) for _ in range(длина))


def main(argv: list[str]) -> None:
    if not argv:
        print(__doc__)
        return
    роль = argv[0]
    пароль = argv[1] if len(argv) > 1 else сгенерировать()

    with db.connect("gisadmin", autocommit=True) as conn:
        есть = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (роль,)
        ).fetchone()
        if not есть:
            raise SystemExit(f"роли {роль} в базе нет — сначала накатите миграцию")
        # ALTER ROLE параметров не принимает: и роль, и пароль подставляются
        # в текст запроса, поэтому оба экранируются средствами psycopg.
        conn.execute(sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(
            sql.Identifier(роль), sql.Literal(пароль)))

    файл = db.SECRETS_DIR / f"{роль}.pw"
    файл.parent.mkdir(parents=True, exist_ok=True)
    файл.write_text(пароль, encoding="utf-8")
    print(f"пароль роли {роль} обновлён, записан в {файл}")


if __name__ == "__main__":
    main(sys.argv[1:])
