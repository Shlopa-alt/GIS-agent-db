"""Машинные проверки признаков: важность и переносимость из расчётов проекта.

Паспорт признака обещает два поля, которых никто не заполняет:
`importance_checked` — ноль из 240, `transferability` — «не проверен» у 135 из
240. Заполнить их задним числом нельзя: все паспорта подтверждены, а правило
базы не даёт роли агента менять подтверждённую строку. Да и по смыслу «важность
посчитана в прогоне N по таблице M» — факт о расчёте, а не суждение о признаке:
он проверяется механически и подтверждения человеком не требует.

Поэтому факты ложатся в `meta.feature_check` (миграция 015), а паспорт остаётся
тем, что подтвердил человек.

Откуда берутся числа — из уже посчитанного в репозитории прогноза:

* важность — `outputs/metrics/paleo_factor_importance_v6.csv`: признак, вклад,
  группа (перестановочная важность на dataset_v6);
* переносимость — `data/processed/honest_feature_set.json`: набор из 58
  признаков, прошедших проверку с разрывом 15 км, набор переноса из 37 и шесть
  служебных столбцов съёмки, исключённых как география покрытия.

Чего в этих файлах нет — то и остаётся непроверенным: строки не пишется вовсе.
Пустая проверка честнее выдуманного вердикта, и в сводке это видно.

    python scripts/db_fill_checks.py --сухой     показать, ничего не записывая
    python scripts/db_fill_checks.py
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gisdb import db  # noqa: E402
from gisdb import db_ingest as ingest  # noqa: E402
from gisdb.project import подключить  # noqa: E402

СКРИПТ = "scripts/db_fill_checks.py"

ВАЖНОСТЬ = Path("outputs/metrics/paleo_factor_importance_v6.csv")
НАБОРЫ = Path("data/processed/honest_feature_set.json")


def важность(корень: Path) -> dict[str, float]:
    """Признак → вклад в прогноз по таблице перестановочной важности."""
    путь = корень / ВАЖНОСТЬ
    if not путь.is_file():
        raise RuntimeError(f"нет таблицы важности: {путь}")
    с_числами: dict[str, float] = {}
    with путь.open(encoding="utf-8-sig", newline="") as ф:
        for строка in csv.DictReader(ф):
            код = (строка.get("признак") or "").strip()
            try:
                с_числами[код] = float(строка["важность"])
            except (TypeError, ValueError, KeyError):
                continue
    return с_числами


def переносимость(корень: Path) -> tuple[dict[str, str], dict]:
    """Признак → вердикт о переносимости и сам разбор набора (для пометок)."""
    путь = корень / НАБОРЫ
    if not путь.is_file():
        raise RuntimeError(f"нет описания наборов: {путь}")
    набор = json.loads(путь.read_text(encoding="utf-8"))
    вердикты: dict[str, str] = {}
    for код in набор.get("признаки", []):
        вердикты[код] = "переносим на основном листе"
    for код in набор.get("признаки_перенос", []):
        вердикты[код] = "переносим, входит в набор переноса"
    for код in набор.get("служебные_исключены", []):
        вердикты[код] = "служебный столбец съёмки, исключён"
    return вердикты, набор


def признаки_базы(conn) -> dict[str, list[int]]:
    """Код признака → номера всех его сборок: проверка относится к коду."""
    строки = conn.execute("SELECT code, id FROM data.feature ORDER BY code, id").fetchall()
    по_коду: dict[str, list[int]] = {}
    for код, номер in строки:
        по_коду.setdefault(код, []).append(номер)
    return по_коду


def записать(conn, run_id: int, номер: int, вид: str, вердикт: str,
             значение: float | None, ссылка: str, отпечаток: str, пометка: str | None) -> None:
    conn.execute(
        """INSERT INTO meta.feature_check
               (feature_id, kind, verdict, value, source_ref, source_hash, note, run_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (feature_id, kind) DO UPDATE SET
               verdict = EXCLUDED.verdict, value = EXCLUDED.value,
               source_ref = EXCLUDED.source_ref, source_hash = EXCLUDED.source_hash,
               note = EXCLUDED.note, run_id = EXCLUDED.run_id, checked_at = now()""",
        (номер, вид, вердикт, значение, ссылка, отпечаток, пометка, run_id),
    )


def main(аргументы: list[str] | None = None) -> int:
    разбор = argparse.ArgumentParser(description="Машинные проверки признаков")
    разбор.add_argument("--сухой", action="store_true", help="показать, ничего не записывая")
    ключи = разбор.parse_args(аргументы)

    корень = подключить()
    вклады = важность(корень)
    вердикты, набор = переносимость(корень)

    with db.connect("gis_agent") as conn:
        по_коду = признаки_базы(conn)
        всего_кодов = len(по_коду)
        с_важностью = sorted(set(вклады) & set(по_коду))
        с_переносом = sorted(set(вердикты) & set(по_коду))
        без_важности = sorted(set(по_коду) - set(вклады))

        print(f"кодов признаков в базе: {всего_кодов}")
        print(f"есть расчёт важности: {len(с_важностью)}; нет: {len(без_важности)}")
        print(f"есть вердикт о переносимости: {len(с_переносом)}")
        print(f"критерий переносимости: {набор.get('критерий')}")
        if ключи.сухой:
            print("\nбез расчёта важности остаются:")
            print("  " + ", ".join(без_важности[:40]) + (" …" if len(без_важности) > 40 else ""))
            примеры = [(к, вклады[к], вердикты.get(к, "—")) for к in с_важностью[:5]]
            print("\nпримеры записей:")
            for код, вклад, вердикт in примеры:
                print(f"  {код}: важность {вклад:+.4f}; переносимость — {вердикт}")
            print("\nсухой прогон: ничего не записано")
            return 0

        путь_важности, путь_наборов = корень / ВАЖНОСТЬ, корень / НАБОРЫ
        ссылка_важности = db.store_ref(путь_важности)
        ссылка_наборов = db.store_ref(путь_наборов)
        хеш_важности = ingest.checksum(путь_важности)
        хеш_наборов = ingest.checksum(путь_наборов)

        записей = 0
        with ingest.run(conn, script=СКРИПТ,
                        note=f"проверки признаков: важность {len(с_важностью)}, "
                             f"переносимость {len(с_переносом)}") as run_id:
            conn.execute(
                """INSERT INTO runs.run_input (run_id, kind, ref_text, checksum)
                   VALUES (%s, 'таблица', %s, %s), (%s, 'таблица', %s, %s)""",
                (run_id, ссылка_важности, хеш_важности, run_id, ссылка_наборов, хеш_наборов),
            )
            for код in с_важностью:
                вклад = вклады[код]
                вердикт = "вносит вклад" if вклад > 0 else "вклада не вносит"
                for номер in по_коду[код]:
                    записать(conn, run_id, номер, "важность", вердикт, вклад,
                             ссылка_важности, хеш_важности,
                             "перестановочная важность на сборке v6")
                    записей += 1
            for код in с_переносом:
                for номер in по_коду[код]:
                    записать(conn, run_id, номер, "переносимость", вердикты[код], None,
                             ссылка_наборов, хеш_наборов, набор.get("критерий"))
                    записей += 1

            ingest.add_metric(conn, run_id, "проверок записано", записей)
            ingest.add_metric(conn, run_id, "кодов без важности", len(без_важности))
            conn.commit()
            print(f"\nзаписано проверок: {записей}; прогон #{run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
