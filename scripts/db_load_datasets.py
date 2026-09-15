"""Перенос существующих датасетов в базу: основной лист и широкая территория.

Запуск: python scripts/db_load_datasets.py [dataset_v6|dataset_wide|all]

Каждый датасет грузится как отдельная сборка: один и тот же код признака в
`dataset_v6` и `dataset_wide` — разные версии, потому что различаются сырьё
и параметры сборки (см. записку о переносе на смежную территорию).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gisdb.db as db
import gisdb.db_ingest as ingest

PROCESSED = Path("data/processed")

ЗАДАНИЯ = {
    "dataset_v6": dict(
        path=PROCESSED / "dataset_v6.parquet",
        grid_code="лист-R-49-I-II-500м",
        cell_size_m=500,
        territory_code="R-49-I,II",
        territory_title="Основной лист, оцифрованная золотая зона",
        is_primary=True,
    ),
    "dataset_wide": dict(
        path=PROCESSED / "dataset_wide.parquet",
        grid_code="широкая-ГГК-200-500м",
        cell_size_m=500,
        territory_code="ГГК-200-широкая",
        territory_title="Смежные листы ГГК-200, территория переноса",
        is_primary=False,
    ),
}


def main(what: str = "all") -> None:
    имена = list(ЗАДАНИЯ) if what == "all" else [what]
    with db.connect("gis_agent") as conn:
        with ingest.run(
            conn,
            script="scripts/db_load_datasets.py",
            note=f"перенос датасетов в базу: {', '.join(имена)}",
        ) as run_id:
            for имя in имена:
                задание = ЗАДАНИЯ[имя]
                print(f"\n=== {имя}: {задание['path']}", flush=True)
                итог = ingest.load_dataset(conn, run_id, **задание)
                print(
                    f"--- {имя}: ячеек {итог['ячеек']}, признаков {итог['признаков']},"
                    f" значений {итог['значений']}",
                    flush=True,
                )
            print(f"\nпрогон #{run_id} записан в runs.run")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
