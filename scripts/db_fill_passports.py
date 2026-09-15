"""Черновики паспортов признаков — механическая часть, без модели.

Заполняется только то, что уже известно из самих данных и из словарей
проекта: покрытие и пропуски (считаются по базе), зависимость от контура
съёмки, циркулярность (прямые входы критериальной формулы), переносимость
(проверка через разрыв 15 км), минерагеническая трактовка.

Всё пишется со статусом «черновик»: геологический смысл подтверждает человек,
и база этого не позволяет обойти (триггер `meta.guard_confirmation`).

Запуск: python scripts/db_fill_passports.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gisdb.db as db
import gisdb.db_ingest as ingest

PROCESSED = Path("data/processed")

# Группы, где значение измерено съёмкой с собственным контуром покрытия.
СЪЁМОЧНЫЕ_ГРУППЫ = {"ast", "astir", "s2", "s2raw", "l8", "s1", "psr", "ls"}

# Служебные столбцы съёмки: это география покрытия, а не геология.
СЛУЖЕБНЫЕ = ("_valid_frac", "_n_obs")


def словари() -> dict:
    """Списки признаков, накопленные в предыдущих работах проекта."""
    честный = json.loads(
        (PROCESSED / "honest_feature_set.json").read_text(encoding="utf-8")
    )
    минерагенический = json.loads(
        (PROCESSED / "mineragenic_feature_set.json").read_text(encoding="utf-8")
    )
    класс_признака = {
        код: класс
        for класс, коды in минерагенический["классы"].items()
        for код in коды
    }
    return {
        "отобранные": set(честный["признаки"]),
        "переносимые": set(честный["признаки_перенос"]),
        "факторные": set(минерагенический["факторные_слои_организации"]["признаки"]),
        "класс": класс_признака,
    }


def паспорт(код: str, покрытие: float, слов: dict) -> dict:
    """Собрать черновик паспорта одного признака."""
    группа = ingest.group_of(код) or ""
    служебный = код.endswith(СЛУЖЕБНЫЕ)

    циркулярность = код in слов["факторные"] or группа in ("geo", "geo2")

    if код in слов["переносимые"]:
        переносимость = "переносим"
    elif код in слов["отобранные"]:
        # Прошёл отбор на листе, но в набор для смежной территории не вошёл.
        переносимость = "не проверен"
    elif группа in ("gm", "pf"):
        переносимость = "непереносим"
    else:
        переносимость = "не проверен"

    if служебный:
        смысл = (
            "Служебный столбец съёмки: доля валидных наблюдений или их число. "
            "География покрытия, а не геология; в обучение не берётся."
        )
    elif код in слов["класс"]:
        смысл = f"Минерагеническая трактовка, класс: {слов['класс'][код]}."
    elif циркулярность:
        смысл = (
            "Факторный слой критериального анализа: прямой вход формулы. "
            "Как признак независимой модели даёт утечку."
        )
    else:
        смысл = None  # заполняет агент или человек

    return {
        "geological_meaning": смысл,
        "aggregation_rule": "среднее по ячейке 500 м общей сетки проекта",
        "coverage_frac": покрытие,
        "missing_frac": round(1 - покрытие, 6),
        "footprint_dependent": группа in СЪЁМОЧНЫЕ_ГРУППЫ or покрытие < 0.999,
        "circularity": циркулярность,
        "transferability": переносимость,
        "model": None,
    }


def main() -> None:
    слов = словари()
    with db.connect("gis_agent") as conn:
        with ingest.run(
            conn,
            script="scripts/db_fill_passports.py",
            note="черновики паспортов признаков по данным базы и словарям проекта",
        ) as run_id:
            строки = conn.execute(
                """SELECT f.id, f.code, f.definition->>'сборка' AS сборка,
                          coalesce(v.n, 0)::float / c.n AS покрытие
                     FROM data.feature f
                     JOIN data.grid g ON g.code = f.definition->>'сетка'
                     JOIN (SELECT grid_id, count(*) AS n
                             FROM data.cell GROUP BY grid_id) c ON c.grid_id = g.id
                     LEFT JOIN (SELECT feature_id, count(*) AS n
                                  FROM data.feature_value GROUP BY feature_id) v
                            ON v.feature_id = f.id
                    ORDER BY f.code"""
            ).fetchall()

            записано = 0
            for feature_id, код, сборка, покрытие in строки:
                п = паспорт(код, round(float(покрытие), 6), слов)
                conn.execute(
                    """INSERT INTO meta.feature_passport
                           (feature_id, geological_meaning, aggregation_rule,
                            coverage_frac, missing_frac, footprint_dependent,
                            circularity, transferability, model)
                       VALUES (%(id)s, %(geological_meaning)s, %(aggregation_rule)s,
                               %(coverage_frac)s, %(missing_frac)s,
                               %(footprint_dependent)s, %(circularity)s,
                               %(transferability)s, %(model)s)
                       ON CONFLICT (feature_id) DO UPDATE SET
                            geological_meaning = excluded.geological_meaning,
                            aggregation_rule   = excluded.aggregation_rule,
                            coverage_frac      = excluded.coverage_frac,
                            missing_frac       = excluded.missing_frac,
                            footprint_dependent= excluded.footprint_dependent,
                            circularity        = excluded.circularity,
                            transferability    = excluded.transferability""",
                    {"id": feature_id, **п},
                )
                записано += 1
            conn.commit()
            ingest.add_metric(conn, run_id, "паспортов-черновиков", записано)
            conn.commit()

            итог = conn.execute(
                """SELECT count(*) FILTER (WHERE geological_meaning IS NOT NULL),
                          count(*) FILTER (WHERE circularity),
                          count(*) FILTER (WHERE transferability = 'переносим'),
                          count(*) FILTER (WHERE footprint_dependent),
                          count(*)
                     FROM meta.feature_passport"""
            ).fetchone()

    print(f"паспортов записано: {записано}")
    print(f"  со смыслом из словарей: {итог[0]}")
    print(f"  помечены циркулярными:  {итог[1]}")
    print(f"  переносимы:             {итог[2]}")
    print(f"  зависят от контура:     {итог[3]}")
    print(f"  всего в таблице:        {итог[4]}")


if __name__ == "__main__":
    main()
