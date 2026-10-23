"""Резервная копия базы — она же комплект для переезда на сервер.

Делает три файла в каталоге `backup` хранилища:

* `gis_au_u_<дата>.dump` — база в сжатом формате `pg_dump -Fc`;
* `roles_<дата>.sql`     — роли и пароли (в дамп базы они не входят);
* `README_<дата>.txt`    — версии и порядок восстановления.

Каждая копия записывается в журнал прогонов тремя артефактами с хешами: без
отпечатка нельзя сказать, ту ли копию проверяли на восстановление, а копия без
обратного пути — просто файл неизвестного назначения.

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
from gisdb import db_ingest as ingest

СКРИПТ = "db/backup.py"


def программа(имя: str) -> Path:
    """Утилита Postgres: переменная сильнее, иначе поиск внутри хранилища."""
    задан = os.environ.get("GIS_DB_BIN")
    return Path(задан) / имя if задан else db.утилита(имя)


def запуск(имя: str, *аргументы: str, вывод: Path | None = None) -> None:
    команда = [str(программа(имя)), "-h", db.HOST, "-p", str(db.PORT), "-U", "gisadmin", *аргументы]
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
        [str(программа("psql")), "-h", db.HOST, "-p", str(db.PORT), "-U", "gisadmin",
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
  5. pg_restore -d {db.DBNAME} --no-owner {дамп.name}
  6. psql -d {db.DBNAME} -f db/sql/004_srid.sql    — своя система координат;
     именно после восстановления: таблица spatial_ref_sys принадлежит PostGIS,
     и накат до pg_restore ломает её COPY на уникальном ключе. Файл
     идемпотентен, шаг нужен для копий, снятых до его появления
  7. Скопировать каталог хранилища (raster, incoming) и задать GIS_DB_ROOT.
  8. python db/migrate.py --состояние              — сверить учёт миграций

Пути файлов в базе записаны как «проект:/...» и «хранилище:/...» —
переносимы, буквы диска в них нет.

Чем сделана копия и проверялась ли она восстановлением — в журнале прогонов
восстановленной базы: таблицы runs.run и runs.artifact, скрипты db/backup.py и
db/restore_check.py.
""",
        encoding="utf-8",
    )

    for файл in (дамп, роли, записка):
        print(f"{файл.name:34} {файл.stat().st_size / 1024 / 1024:8.1f} МБ")

    записать_в_журнал(дамп, роли, записка, метка)


def записать_в_журнал(дамп: Path, роли: Path, записка: Path, метка: str) -> None:
    """Отметить копию в журнале прогонов — после того, как файлы уже на диске.

    Порядок именно такой: копия ценна сама по себе, и неудача записи в журнал
    не должна её отменять. Поэтому сбой здесь — предупреждение, а не падение:
    файлы на месте, недостающую запись можно добавить следующим запуском.
    """
    try:
        with db.connect("gis_agent") as conn:
            with ingest.run(conn, script=СКРИПТ,
                            note=f"копия базы {db.DBNAME} от {метка}") as run_id:
                ingest.add_artifact(conn, run_id, дамп, "копия базы",
                                    "pg_dump -Fc; spatial_ref_sys в дамп не входит")
                ingest.add_artifact(conn, run_id, роли, "роли базы",
                                    "пароли ролей внутри файла")
                ingest.add_artifact(conn, run_id, записка, "описание",
                                    "порядок восстановления и версии сервера")
                ingest.add_metric(conn, run_id, "размер копии, МБ",
                                  дамп.stat().st_size / 1024 / 1024)
                conn.commit()
        print(f"записано в журнал прогонов: #{run_id}")
    except Exception as сбой:                                  # noqa: BLE001
        print(f"копия сделана, но в журнал не записана: {сбой}")


if __name__ == "__main__":
    main()
