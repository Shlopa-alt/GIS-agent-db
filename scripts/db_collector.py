"""Агент-сборщик: что по территории собрано, чего нет и что пересекается с формулой.

Узел 3 технологической схемы — сбор сырых данных. От него требуется не «загрузить
файлы», а три вещи, и все три до сих пор держались в голове:

1. **состав описан** — по территории собран максимум доступного, и состав этого
   максимума должен быть виден не выборкой руками, а ответом базы;
2. **расхождение названо** — зарегистрированный слой, чей файл исчез, и файл,
   которого в базе нет, одинаково выглядят как «всё в порядке», пока их никто не
   сверяет;
3. **пересечение с критериальным анализом зафиксировано отдельно** — метод
   нельзя сравнивать с формулой ГИС Интегро, если часть собранных признаков и
   есть входы этой формулы. Это не повод их выбрасывать, это повод их считать.

Почему это агент, а не отчёт. Отчёт читают и забывают. Здесь каждый осмотр —
прогон в журнале с числами и описью-артефактом: через месяц видно, что база
знала о своём составе на ту дату, и чем он отличается от сегодняшнего. Опись
ложится в `runs.artifact` с отпечатком, как и всякий файл, который база
произвела.

Что сборщик НЕ делает: не регистрирует слои и не грузит значения. Регистрация —
`db_register_layers.py`, загрузка — `db_load_datasets.py`; сборщик говорит, что
из этого стоит запустить, и не запускает сам. Причина простая: регистрация
заводит черновики паспортов и лицензий, то есть работу человеку, и решать,
заводить ли её, должен человек, а не расписание.

Языковая модель здесь не нужна вовсе: всё, что делает сборщик, — это сверка
базы с диском и запросы. Токенов не тратит.

    python scripts/db_collector.py                      осмотр и опись
    python scripts/db_collector.py --территория R-48-XI,XII
    python scripts/db_collector.py --без-диска          не ходить на диск проекта
    python scripts/db_collector.py --куда C:/обмен      опись в другой каталог
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gisdb import db  # noqa: E402
from gisdb import db_ingest as ingest  # noqa: E402

СКРИПТ = "scripts/db_collector.py"

СОСТАВ = """
SELECT t.code AS территория, t.title,
       (SELECT count(*) FROM data.grid g WHERE g.territory_id = t.id) AS сеток,
       (SELECT count(*) FROM data.cell c JOIN data.grid g ON g.id = c.grid_id
         WHERE g.territory_id = t.id) AS ячеек,
       (SELECT count(DISTINCT f.code) FROM data.feature f
          JOIN data.grid g ON g.code = f.definition->>'сетка'
         WHERE g.territory_id = t.id) AS кодов_признаков,
       (SELECT count(*) FROM data.feature_value v
          JOIN data.cell c ON c.id = v.cell_id
          JOIN data.grid g ON g.id = c.grid_id
         WHERE g.territory_id = t.id) AS значений
  FROM data.territory t
 {где}
 ORDER BY t.is_primary DESC, t.code
"""

СЛОИ = """
SELECT l.kind, count(*) AS слоёв,
       count(*) FILTER (WHERE l.evicted) AS выселено,
       count(*) FILTER (WHERE lp.status = 'подтверждён') AS с_паспортом,
       count(*) FILTER (WHERE lic.allows_redistribution) AS можно_отдавать
  FROM data.source_layer l
  LEFT JOIN meta.layer_passport lp ON lp.layer_id = l.id
  LEFT JOIN meta.license lic ON lic.id = lp.license_id
 GROUP BY l.kind ORDER BY 2 DESC
"""

ГРУППЫ = """
SELECT coalesce(f.group_code, 'без группы') AS группа,
       count(DISTINCT f.code) AS кодов,
       count(*) FILTER (WHERE p.status = 'подтверждён') AS паспортов,
       round(avg(p.coverage_frac)::numeric, 3) AS покрытие
  FROM data.feature f
  LEFT JOIN meta.feature_passport p ON p.feature_id = f.id
 WHERE f.is_current
 GROUP BY 1 ORDER BY 2 DESC
"""

#: Пересечение с критериальным анализом: признаки, помеченные как его прямые
#: входы, и слои, из которых они посчитаны. Пометку ставит `db_fill_passports.py`
#: и подтверждает человек — здесь она только собирается в одно место.
ФОРМУЛА = """
SELECT f.code AS признак, coalesce(f.group_code, '') AS группа,
       coalesce(string_agg(DISTINCT l.code, ', '), 'слой не прослежен') AS слои
  FROM meta.feature_passport p
  JOIN data.feature f ON f.id = p.feature_id
  LEFT JOIN meta.derivation d
         ON d.target_kind = 'признак' AND d.target_id = f.id AND d.source_kind = 'слой'
  LEFT JOIN data.source_layer l ON l.id = d.source_id
 WHERE p.circularity AND f.is_current
 GROUP BY f.code, f.group_code ORDER BY f.code
"""

#: Слой зарегистрирован, но файла на диске нет. Выселенные не в счёт: у них на то
#: есть пометка, как их вернуть, — это проверяет отдельное правило базы.
ПУТИ = """
SELECT code, path FROM data.source_layer
 WHERE path IS NOT NULL AND NOT evicted ORDER BY code
"""

#: Сетка, на которую ссылаются признаки, но которой нет в `data.grid`. Такой
#: признак не привязан ни к какой территории: любой запрос «что собрано по
#: территории» его не видит, и пропажа выглядит как «признаков там нет».
СЕТКИ = """
SELECT f.definition->>'сетка' AS сетка, count(DISTINCT f.code) AS кодов
  FROM data.feature f
 WHERE f.definition ? 'сетка'
   AND NOT EXISTS (SELECT 1 FROM data.grid g WHERE g.code = f.definition->>'сетка')
 GROUP BY 1 ORDER BY 2 DESC
"""

ОПИСЬ = """Опись собранных данных базы «{база}» от {дата}

Осмотр выполнен агентом-сборщиком (узел 3 технологической схемы), прогон #{прогон}.
Ничего не изменено: сборщик только сверяет базу с диском и считает.

Состав по территориям
{состав}

Слои-источники по видам
{слои}

Признаки по группам
{группы}

Пересечение с критериальным анализом ГИС Интегро
{формула}

Сверка с диском
{диск}

Сетки признаков, не заведённые в базе
{сетки}
"""


def строки(conn, запрос: str, параметры=()) -> tuple[list[str], list[tuple]]:
    курсор = conn.execute(запрос, параметры)
    return [о.name for о in курсор.description], курсор.fetchall()


def таблицей(заголовок: list[str], ряды: list[tuple], отступ: str = "  ") -> str:
    """Таблица для чтения глазами: ширина столбца по самому длинному значению."""
    если_пусто = f"{отступ}(пусто)"
    if not ряды:
        return если_пусто
    клетки = [[("" if з is None else str(з)) for з in ряд] for ряд in ряды]
    ширины = [max(len(заголовок[i]), *(len(к[i]) for к in клетки))
              for i in range(len(заголовок))]
    out = [отступ + "  ".join(з.ljust(ш) for з, ш in zip(заголовок, ширины)).rstrip(),
           отступ + "  ".join("-" * ш for ш in ширины)]
    out += [отступ + "  ".join(з.ljust(ш) for з, ш in zip(к, ширины)).rstrip()
            for к in клетки]
    return "\n".join(out)


def пропавшие(conn) -> list[tuple[str, str]]:
    """Слои, чьих файлов на диске нет. Путь разрешается через `db.store_path`:
    в базе он записан без буквы диска, и на другой машине это тот же слой."""
    нет = []
    for код, ссылка in conn.execute(ПУТИ).fetchall():
        if not db.store_path(ссылка).exists():
            нет.append((код, ссылка))
    return нет


def лишние(conn, каталоги: tuple[str, ...] = ("data/Gis-integro", "data/SBORKA_DOP")
           ) -> tuple[list[str], list[str]]:
    """Файлы данных, которых в базе нет: новое сырьё и выгрузки — порознь.

    Смотрим только каталоги материалов организации: именно там появляются новые
    поступления, и именно их пропуск означает «собрали не максимум». Открытые
    съёмки сюда не попадают — они приходят запросом, а не файлом.

    Почему порознь. Первый же осмотр нашёл 40 «отсутствующих» файлов, и все
    сорок оказались поканальными выгрузками уже зарегистрированных сеток ГИС
    Интегро: `grav_mag__mag_ost_35.tif` — это канал слоя `грав_маг.pgrid`,
    зарегистрированного целиком. Считать их новым сырьём — значит каждый осмотр
    звать регистрировать то, что уже есть. Сопоставить имена напрямую нельзя:
    сетка названа кириллицей, выгрузка — латиницей. Зато двойное подчёркивание
    в имени — это сам признак выгрузки: так её называет скрипт, который её
    делает. Файл без него считается новым и попадает в список к человеку.
    """
    знакомые = {db.store_path(с).resolve()
                for (с,) in conn.execute(
                    "SELECT path FROM data.source_layer WHERE path IS NOT NULL").fetchall()}
    новое: list[str] = []
    выгрузки: list[str] = []
    for относительный in каталоги:
        каталог = db.КОРЕНЬ_ПРОЕКТА / относительный
        if not каталог.is_dir():
            continue
        for файл in sorted(каталог.rglob("*")):
            if файл.suffix.lower() not in (".shp", ".pgrid", ".tif", ".gpkg"):
                continue
            if файл.resolve() in знакомые:
                continue
            (выгрузки if "__" in файл.stem else новое).append(db.store_ref(файл))
    return новое, выгрузки


def main(аргументы: list[str] | None = None) -> int:
    разбор = argparse.ArgumentParser(description="Агент-сборщик: осмотр собранных данных")
    разбор.add_argument("--территория", default=None, help="только одна территория")
    разбор.add_argument("--без-диска", action="store_true",
                        help="не ходить на диск проекта: только то, что знает база")
    разбор.add_argument("--куда", type=Path, help="каталог для описи")
    ключи = разбор.parse_args(аргументы)

    куда = ключи.куда or (db.ROOT / "опись")

    with db.connect("gis_agent") as conn:
        части: dict[str, str] = {}
        where = "WHERE t.code = %s" if ключи.территория else ""
        параметры = (ключи.территория,) if ключи.территория else ()
        for имя, запрос, пар in (("состав", СОСТАВ.format(где=where), параметры),
                                 ("слои", СЛОИ, ()),
                                 ("группы", ГРУППЫ, ()),
                                 ("формула", ФОРМУЛА, ())):
            заголовок, ряды = строки(conn, запрос, пар)
            части[имя] = таблицей(заголовок, ряды)
            print(f"\n{имя}:")
            print(части[имя])
            if имя == "состав" and not ряды:
                print(f"территории «{ключи.территория}» в базе нет", file=sys.stderr)
                return 1

        входов = len(conn.execute(ФОРМУЛА).fetchall())

        заголовок, ряды = строки(conn, СЕТКИ)
        части["сетки"] = таблицей(заголовок, ряды)
        ничьих = sum(р[1] for р in ряды)
        if ряды:
            части["сетки"] += ("\n  Признаки этих сеток не привязаны ни к одной "
                               "территории: заведите сетку в data.grid или исправьте "
                               "её код в сборке.")
        print("\nсетки признаков, не заведённые в базе:")
        print(части["сетки"])

        # ------------------------------------------------------- сверка с диском
        нет: list[tuple[str, str]] = []
        новые: list[str] = []
        выгрузки: list[str] = []
        if ключи.без_диска:
            части["диск"] = ("  не выполнялась: запущено с ключом --без-диска "
                             "(база не знает, лежит ли файл на этой машине)")
        elif not db.КОРЕНЬ_ПРОЕКТА.is_dir():
            части["диск"] = (f"  не выполнялась: репозитория прогноза нет по пути "
                             f"{db.КОРЕНЬ_ПРОЕКТА} (переменная GIS_PROJECT_ROOT)")
        else:
            нет = пропавшие(conn)
            новые, выгрузки = лишние(conn)
            куски = [f"  слоёв в базе без файла на диске: {len(нет)}"]
            куски += [f"    {к} — {с}" for к, с in нет]
            куски.append(f"  нового сырья, которого в базе нет: {len(новые)}")
            куски += [f"    {с}" for с in новые]
            if новые:
                куски.append("  зарегистрировать: python scripts/db_register_layers.py")
            куски.append(f"  поканальных выгрузок зарегистрированных слоёв: "
                         f"{len(выгрузки)} — регистрации не требуют")
            части["диск"] = "\n".join(куски)
        print(f"\nсверка с диском:\n{части['диск']}")

        # --------------------------------------------------------------- опись
        with ingest.run(conn, script=СКРИПТ,
                        note=f"осмотр состава: входов формулы {входов}, "
                             f"без файла {len(нет)}, нового сырья {len(новые)}") as run_id:
            куда.mkdir(parents=True, exist_ok=True)
            путь = куда / f"опись_{db.DBNAME}_{date.today():%Y%m%d}.txt"
            путь.write_text(
                ОПИСЬ.format(база=db.DBNAME, дата=f"{date.today():%d.%m.%Y}",
                             прогон=run_id, **части),
                encoding="utf-8",
            )
            ingest.add_artifact(conn, run_id, путь, "опись",
                                "состав собранных данных и сверка с диском")
            ingest.add_metric(conn, run_id, "входов критериальной формулы", входов)
            ingest.add_metric(conn, run_id, "слоёв без файла", len(нет))
            ingest.add_metric(conn, run_id, "нового сырья не в базе", len(новые))
            ingest.add_metric(conn, run_id, "выгрузок зарегистрированных слоёв", len(выгрузки))
            ingest.add_metric(conn, run_id, "кодов признаков без сетки", ничьих)
            conn.commit()

    print(f"\nопись: {путь}")
    print(f"прогон #{run_id}; осмотр ничего не изменил")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
