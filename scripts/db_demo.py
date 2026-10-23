"""Образец базы: то же устройство, только распространяемые данные.

Зачем. Показать базу можно двумя способами: рассказать о ней — или дать её
потрогать. Второе убедительнее, но полную базу отдавать нельзя: 34 слоя из 40
переданы организацией или защищены условиями поставщиков. Выгрузка в CSV
(`scripts/db_export.py`) эту проблему не решает — она отдаёт таблицы, а
показать надо работающую систему: панель подтверждений, происхождение, правила,
которые не дают агенту подтвердить свой же черновик.

Образец — это отдельная база с тем же устройством и настоящими данными, но
только теми, которые разрешено распространять. В ней остаются:

* 6 слоёв открытых источников (Sentinel, ASTER/NASA, ALOS/JAXA, Landsat/USGS)
  с паспортами и лицензиями;
* признаки, у которых **все** слои-источники разрешены, с паспортами, смыслом,
  машинными проверками и значениями;
* понятия, связи «признак — понятие», цитаты из опубликованных статей;
* сетки, ячейки, территории;
* журналы прогонов и граф происхождения по оставшимся объектам — то, ради чего
  базу и показывают.

Чего в ней нет: 34 закрытых слоя, признаки, посчитанные из них, и всё, что на
них ссылалось. Удаление идёт в копии, рабочая база не затрагивается; имя базы
образца проверяется отдельно, чтобы скрипт не мог снести рабочую.

Как собирается: последний дамп рабочей базы разворачивается в отдельную базу,
туда накатываются миграции, из неё удаляется запрещённое, затем снимается
собственный дамп образца с описанием. Так образец воспроизводим: он собран из
того же дампа, что лежит в хранилище копий.

    python scripts/db_demo.py --проверить     что останется и что уйдёт
    python scripts/db_demo.py
    python scripts/db_demo.py --оставить      не удалять базу образца после дампа
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

from gisdb import db  # noqa: E402
from gisdb import db_ingest as ingest  # noqa: E402

СКРИПТ = "scripts/db_demo.py"

#: Имя рабочей базы — запоминается при загрузке модуля, до того как `db.DBNAME`
#: переключится на образец. Защита от удаления сверяется именно с ним: иначе
#: она сравнивает образец с образцом и запрещает ровно то, что разрешено.
РАБОЧАЯ_БАЗА = db.DBNAME

#: Имя базы образца. Латиницей и с хвостом `_demo`: хвост проверяется перед
#: каждым удалением — единственная защита от «а я думал, это образец».
ОБРАЗЕЦ = f"{РАБОЧАЯ_БАЗА}_demo"
ХВОСТ = "_demo"

ПРОЧТИ = """Образец базы данных проекта — {база}
Собран {дата} из копии рабочей базы.

Что это
Полностью работающая база того же устройства, что рабочая, но с данными,
которые разрешено распространять. Её можно развернуть, открыть панель
подтверждений, посмотреть паспорта, происхождение и журналы прогонов.

Что внутри
{состав}

Чего внутри нет
{убрано}

Эти данные переданы организацией для выполнения работ или защищены условиями
поставщиков. Признак исключён, если запрещён хотя бы один слой, из которого он
посчитан: значения несут в себе содержимое слоя, пусть и свёрнутое в ячейку.

Лицензии оставшихся источников
{лицензии}

Как развернуть
  1. PostgreSQL {версия_нужна} или новее с PostGIS.
  2. psql -f {роли}                          роли без паролей
  3. createdb -O gisadmin {база}
  4. psql -d {база} -c "CREATE EXTENSION postgis; CREATE EXTENSION postgis_raster;"
  5. pg_restore -d {база} --no-owner {дамп}
  6. psql -d {база} -f db/sql/004_srid.sql    своя система координат
  7. python db/set_password.py gisadmin  (и так же gis_agent, gis_read, gis_human)
  8. python -m gisdb.panel   →  http://127.0.0.1:8765

Паролей ролей в наборе нет: роли создаются без них, пароли задаются на шаге 7
уже у вас. Система координат накатывается после восстановления, а не до:
таблица spatial_ref_sys принадлежит PostGIS, и накат до pg_restore ломает её
загрузку на уникальном ключе.

Схема базы задаётся миграциями в репозитории — в дампе она тоже есть, и после
восстановления `python db/migrate.py --состояние` должен показать «ожидает: 0»:
это и есть проверка, что код и база сошлись.

Что посмотреть в первую очередь
  * панель подтверждений: очередь черновиков и карточка признака — те же
    факты, по которым писала модель, видны человеку;
  * api.summary — состав базы одним запросом;
  * api.redistribution — по каждому признаку: можно ли передавать значения;
  * runs.run и meta.derivation — чем и из чего получен каждый объект.
"""


#: Роли для набора — без паролей. Пароли наших ролей в образец попасть не
#: должны ни в каком виде, а `pg_dumpall --roles-only` выгружает их хеши.
РОЛИ_SQL = """-- Роли для образца базы. Паролей здесь нет намеренно: задайте их
-- после восстановления, командой python db/set_password.py <роль>.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gisadmin') THEN
        CREATE ROLE gisadmin LOGIN CREATEDB CREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gis_agent') THEN
        CREATE ROLE gis_agent LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gis_read') THEN
        CREATE ROLE gis_read LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gis_human') THEN
        CREATE ROLE gis_human LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gis_confirmer') THEN
        CREATE ROLE gis_confirmer NOLOGIN;
    END IF;
END
$$;

-- Право выносить вердикт даётся членством, а не по умолчанию.
GRANT gis_confirmer TO gis_human;
"""


def служебное(база: str = "postgres"):
    return psycopg.connect(host=db.HOST, port=db.PORT, dbname=база, user="gisadmin",
                           password=db.password("gisadmin"), autocommit=True)


def среда() -> dict[str, str]:
    return {**os.environ, "PGPASSWORD": db.password("gisadmin"),
            "PGCLIENTENCODING": "UTF8"}


#: Прогоны этих скриптов не меняют данные — они их читают и складывают в файлы.
#: Из «времени последнего изменения базы» они исключаются, иначе снятие копии
#: само делает копию устаревшей: прогон закрывается после записи файла, то есть
#: всегда на секунду позже него.
НЕ_МЕНЯЮТ = ("db/backup.py", "db/restore_check.py",
             "scripts/db_export.py", "scripts/db_demo.py",
             "scripts/db_check_passports.py")


def копия_свежая(conn, дамп: Path) -> tuple[bool, str]:
    """Не отстала ли копия от рабочей базы.

    Первый собранный образец вышел с нулём строк словаря признаков: копия была
    снята за два часа до того, как словарь загрузили. Снаружи это выглядело бы
    не как устаревшая копия, а как база, которая не знает, что такое её
    собственные признаки. Любая запись в базу проходит прогоном, поэтому время
    последнего изменяющего прогона и есть время последнего изменения.
    """
    последняя = conn.execute(
        """SELECT max(coalesce(finished_at, started_at)) FROM runs.run
            WHERE script <> ALL(%s)""", (list(НЕ_МЕНЯЮТ),)).fetchone()[0]
    снята = datetime.fromtimestamp(дамп.stat().st_mtime).astimezone()
    if последняя is None or снята >= последняя:
        return True, f"копия от {снята:%d.%m.%Y %H:%M} — свежая"
    return False, (f"копия снята {снята:%d.%m.%Y %H:%M}, а база менялась "
                   f"{последняя:%d.%m.%Y %H:%M}")


def проверить_имя(имя: str) -> str:
    """Единственная защита от удаления в рабочей базе — проверять её по имени."""
    if имя == РАБОЧАЯ_БАЗА or not имя.endswith(ХВОСТ):
        raise SystemExit(
            f"база образца должна называться с хвостом «{ХВОСТ}» и не совпадать "
            f"с рабочей ({РАБОЧАЯ_БАЗА}); получено «{имя}». Удаление в рабочей "
            "базе этим скриптом невозможно"
        )
    return имя


# ------------------------------------------------------------------- что уйдёт
ЗАПРЕЩЁННЫЕ_СЛОИ = """
SELECT l.id, l.code, lic.code
  FROM data.source_layer l
  JOIN meta.layer_passport lp ON lp.layer_id = l.id
  JOIN meta.license lic ON lic.id = lp.license_id
 WHERE NOT lic.allows_redistribution
 ORDER BY l.code
"""

ЗАПРЕЩЁННЫЕ_ПРИЗНАКИ = """
SELECT DISTINCT f.id, f.code
  FROM data.feature f
  JOIN meta.derivation d ON d.target_kind = 'признак' AND d.target_id = f.id
                        AND d.source_kind = 'слой'
  JOIN meta.layer_passport lp ON lp.layer_id = d.source_id
  JOIN meta.license lic ON lic.id = lp.license_id
 WHERE NOT lic.allows_redistribution
 ORDER BY f.code
"""


def что_уйдёт(conn) -> tuple[list, list]:
    слои = conn.execute(ЗАПРЕЩЁННЫЕ_СЛОИ).fetchall()
    признаки = conn.execute(ЗАПРЕЩЁННЫЕ_ПРИЗНАКИ).fetchall()
    return слои, признаки


# --------------------------------------------------------------------- чистка
def вычистить(conn, имя_базы: str) -> list[tuple[str, int]]:
    """Удалить запрещённое к распространению. Порядок — по зависимостям."""
    проверить_имя(имя_базы)
    слои, признаки = что_уйдёт(conn)
    слои_id = [и for и, *_ in слои]
    признаки_id = [и for и, _ in признаки]
    убрано: list[tuple[str, int]] = []

    def удалить(что: str, запрос: str, параметры: tuple) -> None:
        сколько = conn.execute(запрос, параметры).rowcount
        убрано.append((что, сколько))

    if признаки_id:
        # Значения — первыми: на них ссылаются разделы таблицы значений, и
        # удалять признак, пока они есть, не даст внешний ключ.
        удалить("значений признаков", "DELETE FROM data.feature_value WHERE feature_id = ANY(%s)",
                (признаки_id,))
        удалить("машинных проверок", "DELETE FROM meta.feature_check WHERE feature_id = ANY(%s)",
                (признаки_id,))
        удалить("связей признак-понятие",
                "DELETE FROM bridge.feature_concept WHERE feature_id = ANY(%s)", (признаки_id,))
        удалить("паспортов признаков",
                "DELETE FROM meta.feature_passport WHERE feature_id = ANY(%s)", (признаки_id,))
        удалить("решений по признакам",
                """DELETE FROM meta.review WHERE object_kind IN ('паспорт признака',
                       'связь признак-понятие') AND object_id = ANY(%s)""", (признаки_id,))
        удалить("рёбер происхождения (признаки)",
                """DELETE FROM meta.derivation
                    WHERE (target_kind = 'признак' AND target_id = ANY(%s))
                       OR (source_kind = 'признак' AND source_id = ANY(%s))""",
                (признаки_id, признаки_id))
        # Словарь признаков — по коду: он не связан внешним ключом, и
        # оставленная строка описывала бы признак, которого в базе нет.
        удалить("строк словаря признаков",
                "DELETE FROM meta.feature_term WHERE code = ANY(%s)",
                ([к for _, к in признаки],))
        удалить("признаков", "DELETE FROM data.feature WHERE id = ANY(%s)", (признаки_id,))

    if слои_id:
        удалить("рёбер происхождения (слои)",
                "DELETE FROM meta.derivation WHERE source_kind = 'слой' AND source_id = ANY(%s)",
                (слои_id,))
        удалить("решений по слоям",
                "DELETE FROM meta.review WHERE object_kind = 'паспорт слоя' AND object_id = ANY(%s)",
                (слои_id,))
        удалить("паспортов слоёв",
                "DELETE FROM meta.layer_passport WHERE layer_id = ANY(%s)", (слои_id,))
        # Входы прогонов называют файлы слоёв по путям. Сам файл в образец не
        # попадает, но и путь к закрытому материалу показывать незачем.
        удалить("входов прогонов на закрытые файлы",
                """DELETE FROM runs.run_input i
                    WHERE EXISTS (SELECT 1 FROM data.source_layer l
                                   WHERE l.id = ANY(%s) AND l.path IS NOT NULL
                                     AND i.ref_text LIKE '%%' || l.code || '%%')""",
                (слои_id,))
        удалить("слоёв-источников", "DELETE FROM data.source_layer WHERE id = ANY(%s)",
                (слои_id,))

    # Лицензии, под которыми не осталось слоёв: сами по себе они не секрет, но
    # в образце не должно быть упоминаний того, чего в нём нет.
    удалить("неиспользуемых лицензий",
            """DELETE FROM meta.license lic
                WHERE NOT EXISTS (SELECT 1 FROM meta.layer_passport lp
                                   WHERE lp.license_id = lic.id)""", ())
    удалить("решений по удалённым лицензиям",
            """DELETE FROM meta.review r WHERE r.object_kind = 'лицензия'
                AND NOT EXISTS (SELECT 1 FROM meta.license lic WHERE lic.id = r.object_id)""", ())
    conn.commit()
    return убрано


# --------------------------------------------------------------------- сверка
def сверить(conn) -> list[tuple[str, str, bool]]:
    закрытых_слоёв = conn.execute(
        """SELECT count(*) FROM meta.layer_passport lp
             JOIN meta.license lic ON lic.id = lp.license_id
            WHERE NOT lic.allows_redistribution"""
    ).fetchone()[0]
    запрещённых = conn.execute(
        "SELECT count(*) FROM api.redistribution WHERE NOT можно_распространять"
    ).fetchone()[0]
    висячих = conn.execute(
        """SELECT count(*) FROM meta.derivation d
            WHERE d.source_kind = 'слой'
              AND NOT EXISTS (SELECT 1 FROM data.source_layer l WHERE l.id = d.source_id)"""
    ).fetchone()[0]
    без_паспорта = conn.execute(
        """SELECT count(*) FROM data.feature f
            WHERE NOT EXISTS (SELECT 1 FROM meta.feature_passport p
                               WHERE p.feature_id = f.id)"""
    ).fetchone()[0]
    признаков = conn.execute("SELECT count(*) FROM api.feature").fetchone()[0]
    значений = conn.execute("SELECT count(*) FROM data.feature_value").fetchone()[0]
    return [
        ("закрытых слоёв осталось", str(закрытых_слоёв), закрытых_слоёв == 0),
        ("признаков с запретом осталось", str(запрещённых), запрещённых == 0),
        ("рёбер на удалённые слои", str(висячих), висячих == 0),
        ("признаков без паспорта", str(без_паспорта), без_паспорта == 0),
        ("признаков в образце", str(признаков), признаков > 0),
        ("значений в образце", f"{значений:,}".replace(",", " "), значений > 0),
    ]


def main(аргументы: list[str] | None = None) -> int:
    разбор = argparse.ArgumentParser(description="Образец базы для показа")
    разбор.add_argument("--база", default=ОБРАЗЕЦ, help=f"имя базы образца ({ОБРАЗЕЦ})")
    разбор.add_argument("--проверить", action="store_true",
                        help="показать, что останется и что уйдёт, ничего не делая")
    разбор.add_argument("--оставить", action="store_true",
                        help="не удалять базу образца после снятия дампа")
    разбор.add_argument("--из-последней-копии", dest="как_есть", action="store_true",
                        help="собрать из имеющейся копии, даже если она отстала")
    ключи = разбор.parse_args(аргументы)
    имя = проверить_имя(ключи.база)

    with db.connect("gis_read") as рабочая:
        слои, признаки = что_уйдёт(рабочая)
        всего_слоёв = рабочая.execute("SELECT count(*) FROM data.source_layer").fetchone()[0]
        всего_признаков = рабочая.execute("SELECT count(*) FROM data.feature").fetchone()[0]
        print(f"рабочая база: слоёв {всего_слоёв}, признаков {всего_признаков}")
        print(f"уйдёт из образца: слоёв {len(слои)}, признаков {len(признаки)}")
        по_лицензиям: dict[str, int] = {}
        for _, _, лиц in слои:
            по_лицензиям[лиц] = по_лицензиям.get(лиц, 0) + 1
        for лиц, сколько in sorted(по_лицензиям.items(), key=lambda п: -п[1]):
            print(f"    {лиц}: {сколько}")
        print(f"останется: слоёв {всего_слоёв - len(слои)}, "
              f"признаков {всего_признаков - len(признаки)}")
        print(f"база образца: {имя}")

        копии = sorted((db.ROOT / "backup").glob(f"{РАБОЧАЯ_БАЗА}_*.dump"))
        свежая, про_копию = копия_свежая(рабочая, копии[-1]) if копии \
            else (False, "копий рабочей базы нет")
        print(про_копию)
        if ключи.проверить:
            print("\nпроверка: ничего не сделано")
            return 0
        if not копии:
            raise SystemExit("нет копии рабочей базы: сперва python db/backup.py")
        if not свежая and not ключи.как_есть:
            raise SystemExit(
                "образец собирался бы из устаревшей копии — в нём не будет "
                "того,\nчто добавили в базу после неё. Снять свежую копию:\n"
                "    python db/backup.py\n"
                "Либо собрать как есть, осознанно: --из-последней-копии"
            )

    источник = копии[-1]
    print(f"\nисточник: {источник.name}", flush=True)

    with служебное() as служба:
        служба.execute(f'DROP DATABASE IF EXISTS "{имя}" WITH (FORCE)')
        служба.execute(f'CREATE DATABASE "{имя}" OWNER gisadmin')
    subprocess.run(
        [str(db.утилита("psql")), "-h", db.HOST, "-p", str(db.PORT), "-U", "gisadmin",
         "-d", имя, "-q", "-c",
         "CREATE EXTENSION postgis; CREATE EXTENSION postgis_raster;"],
        env=среда(), check=True, capture_output=True)
    итог = subprocess.run(
        [str(db.утилита("pg_restore")), "-h", db.HOST, "-p", str(db.PORT), "-U", "gisadmin",
         "-d", имя, "--no-owner", "-j", "4", str(источник)],
        env=среда(), capture_output=True)
    print(f"копия развёрнута (pg_restore {итог.returncode})", flush=True)

    # Дамп может быть старше последних миграций — схему доводим до кода.
    db.DBNAME = имя
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "db"))
    import migrate

    if migrate.main([]):
        raise SystemExit("схема образца не доведена до кода")

    print("\nчистка:", flush=True)
    with db.connect("gisadmin") as образец:
        for что, сколько in вычистить(образец, имя):
            print(f"  {что:38} {сколько:9}")
        print("\nсжатие и пересчёт статистики…", flush=True)
    with служебное(имя) as образец:
        # VACUUM FULL — чтобы удалённое не уехало в дампе мёртвыми строками.
        образец.execute("VACUUM FULL ANALYZE")

    print("\nсверка образца:")
    with db.connect("gis_read") as образец:
        плохо = 0
        for что, значение, ладно in сверить(образец):
            плохо += not ладно
            print(f"  {что:34} {значение:14} {'да' if ладно else 'НЕТ'}")
        состав = образец.execute("SELECT что, сколько FROM api.summary").fetchall()
        лицензии = образец.execute(
            """SELECT лицензия, лицензия_название, лицензия_адрес, count(*)
                 FROM api.layer GROUP BY 1, 2, 3 ORDER BY 4 DESC"""
        ).fetchall()
        версия = образец.execute("SHOW server_version").fetchone()[0].split(".")[0]
    if плохо:
        print("\nобразец собран с замечаниями — не передавать, разбираться")
        return 1

    каталог = db.ROOT / "образец"
    каталог.mkdir(parents=True, exist_ok=True)
    дамп = каталог / f"{имя}_{date.today():%Y%m%d}.dump"
    итог = subprocess.run(
        [str(db.утилита("pg_dump")), "-h", db.HOST, "-p", str(db.PORT), "-U", "gisadmin",
         "-d", имя, "-Fc", "-Z", "6", "-f", str(дамп)],
        env=среда(), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if итог.returncode:
        raise SystemExit(f"pg_dump образца вернул {итог.returncode}: {итог.stderr[:400]}")

    роли = каталог / "роли.sql"
    роли.write_text(РОЛИ_SQL, encoding="utf-8")

    прочти = каталог / f"ПРОЧТИ_{date.today():%Y%m%d}.txt"
    прочти.write_text(
        ПРОЧТИ.format(
            база=имя, дата=f"{date.today():%d.%m.%Y}", версия_нужна=версия,
            дамп=дамп.name, роли=роли.name,
            состав="\n".join(f"  {ч:40} {с}" for ч, с in состав),
            убрано=(f"  закрытых слоёв-источников: {len(слои)} из {всего_слоёв}\n"
                    f"  признаков, посчитанных из них: {len(признаки)} из {всего_признаков}"),
            лицензии="\n".join(
                f"  {к} — {н}" + (f"\n    {а}" if а else "") for к, н, а, _ in лицензии),
        ),
        encoding="utf-8",
    )

    # Сборка образца записывается в журнал РАБОЧЕЙ базы: там наше происхождение.
    db.DBNAME = РАБОЧАЯ_БАЗА
    with db.connect("gis_agent") as рабочая:
        with ingest.run(рабочая, script=СКРИПТ,
                        note=f"образец для показа: слоёв {всего_слоёв - len(слои)}, "
                             f"признаков {всего_признаков - len(признаки)}") as run_id:
            ingest.add_input(рабочая, run_id, источник, kind="копия базы")
            ingest.add_artifact(рабочая, run_id, дамп, "образец базы",
                                "схема и данные; паролей ролей внутри нет")
            ingest.add_artifact(рабочая, run_id, прочти, "описание",
                                "что в образце, чего нет и как развернуть")
            ingest.add_artifact(рабочая, run_id, роли, "роли базы",
                                "без паролей: их задаёт тот, кто разворачивает")
            ingest.add_metric(рабочая, run_id, "слоёв исключено", len(слои))
            ingest.add_metric(рабочая, run_id, "признаков исключено", len(признаки))
            ingest.add_metric(рабочая, run_id, "размер образца, МБ",
                              дамп.stat().st_size / 2**20)
            рабочая.commit()

    if not ключи.оставить:
        with служебное() as служба:
            проверить_имя(имя)
            служба.execute(f'DROP DATABASE IF EXISTS "{имя}" WITH (FORCE)')
        print(f"\nбаза образца {имя} удалена — дамп остался")
    else:
        print(f"\nбаза образца {имя} оставлена на сервере")

    print(f"образец: {дамп} ({дамп.stat().st_size / 2**20:.0f} МБ)")
    print(f"описание: {прочти}")
    print(f"прогон #{run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
