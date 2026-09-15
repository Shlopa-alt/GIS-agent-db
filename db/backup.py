"""Резервная копия базы — она же комплект для переезда на сервер.

Делает три файла в каталоге `backup` хранилища:

* `gis_au_u_<дата>.dump` — база в сжатом формате `pg_dump -Fc`;
* `roles_<дата>.sql`     — роли и пароли (в дамп базы они не входят);
* `README_<дата>.txt`    — версии и порядок восстановления.

Запуск: python db/backup.py
Переменная `GIS_DB_BIN` задаёт каталог с `pg_dump`, если он не на месте.
"""

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gisdb import db

BIN = Path(os.environ.get("GIS_DB_BIN", db.ROOT / "pg18" / "bin"))


def запуск(программа: str, *аргументы: str, вывод: Path | None = None) -> None:
    команда = [str(BIN / программа), "-h", db.HOST, "-p", str(db.PORT), "-U", "gisadmin", *аргументы]
    окружение = {**os.environ, "PGPASSWORD": db.password("gisadmin")}
    if вывод is None:
        subprocess.run(команда, env=окружение, check=True)
    else:
        with open(вывод, "w", encoding="utf-8") as f:
            subprocess.run(команда, env=окружение, check=True, stdout=f)


def main() -> None:
    db.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    метка = date.today().strftime("%Y%m%d")

    дамп = db.BACKUP_DIR / f"{db.DBNAME}_{метка}.dump"
    роли = db.BACKUP_DIR / f"roles_{метка}.sql"
    записка = db.BACKUP_DIR / f"README_{метка}.txt"

    запуск("pg_dump", "-Fc", "-Z", "6", "-f", str(дамп), db.DBNAME)
    запуск("pg_dumpall", "--roles-only", вывод=роли)

    версия = subprocess.run(
        [str(BIN / "psql"), "-h", db.HOST, "-p", str(db.PORT), "-U", "gisadmin",
         "-d", db.DBNAME, "-tAc",
         "SELECT version() || ' | PostGIS ' || postgis_version()"],
        env={**os.environ, "PGPASSWORD": db.password("gisadmin")},
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.strip()

    записка.write_text(
        f"""Копия базы {db.DBNAME} от {метка}

{версия}

Восстановление на новом сервере:
  1. Поставить PostgreSQL не ниже этой версии с PostGIS.
  2. psql -f roles_{метка}.sql                     — роли и пароли
  3. createdb -O gisadmin {db.DBNAME}
  4. psql -d {db.DBNAME} -c "CREATE EXTENSION postgis; CREATE EXTENSION postgis_raster;"
  5. psql -d {db.DBNAME} -f db/sql/004_srid.sql    — своя система координат:
     содержимое spatial_ref_sys принадлежит расширению и в дамп НЕ попадает
  6. pg_restore -d {db.DBNAME} --no-owner {дамп.name}
  7. Скопировать каталог хранилища (raster, incoming) и задать GIS_DB_ROOT.

Пути файлов в базе записаны как «проект:/...» и «хранилище:/...» —
переносимы, буквы диска в них нет.
""",
        encoding="utf-8",
    )

    for файл in (дамп, роли, записка):
        print(f"{файл.name:34} {файл.stat().st_size / 1024 / 1024:8.1f} МБ")


if __name__ == "__main__":
    main()
