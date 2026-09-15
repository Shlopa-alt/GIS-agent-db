"""Загрузка данных проекта в базу: сетки, ячейки, признаки, значения.

Каждая загрузка пишется в журнал прогонов (`runs.run`) с хешами входных
файлов, а связь «файл → признак» — в граф происхождения (`meta.operation`,
`meta.derivation`). Работает от роли `gis_agent`: вставка и изменение без
удаления.

Сетки проекта заданы в собственной системе координат SRID 990018 (поперечная
Меркатора, осевой меридиан 105°, эллипсоид Красовского) — см.
`db/sql/004_srid.sql`. Геометрия ячеек хранится в WGS84.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd
import psycopg

from . import db

GRID_SRID: int = 990018

# Служебные колонки датасетов: это адрес ячейки, а не признаки.
ADDRESS_COLUMNS: tuple[str, ...] = ("row", "col", "x", "y")


# --------------------------------------------------------------- вспомогательное
def checksum(path: Path, chunk: int = 1 << 20) -> str:
    """SHA-256 файла; им сверяем, что вход прогона не подменился."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def git_commit() -> str | None:
    """Текущий коммит репозитория — версия кода, породившего данные."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except Exception:
        return None


def group_of(code: str) -> str | None:
    """Группа признака по префиксу кода: `ast_kaolin` → `ast`."""
    return code.split("_", 1)[0] if "_" in code else None


# -------------------------------------------------------------- журнал прогонов
БРОШЕННЫЕ = """
UPDATE runs.run
   SET status = 'оборван',
       finished_at = coalesce(
           (SELECT max(at) FROM runs.llm_call WHERE run_id = runs.run.id), now()),
       note = coalesce(note || ' — ', '') || 'прогон прерван, запись закрыта следующим запуском'
 WHERE status = 'идёт' AND script = %s AND started_at < now() - interval '1 hour'
"""


def оборвать_брошенные(conn: psycopg.Connection, script: str) -> int:
    """Закрыть записи прогонов, оставшиеся открытыми после снятого процесса.

    Процесс, убитый извне, не успевает поставить ни «успех», ни «сбой», и
    прогон навсегда остаётся «идёт» — журнал перестаёт отвечать на вопрос
    «чем кончилось». Следующий запуск того же скрипта закрывает такие записи
    статусом «оборван», а временем окончания берёт последнее обращение к
    модели: это ближе к правде, чем момент уборки.

    Час выдержки — чтобы не тронуть прогон, идущий в соседнем окне.
    """
    затронуто = conn.execute(БРОШЕННЫЕ, (script,)).rowcount
    conn.commit()
    return затронуто


@contextmanager
def run(
    conn: psycopg.Connection,
    script: str,
    note: str | None = None,
    model: str | None = None,
) -> Iterator[int]:
    """Открыть запись прогона, закрыть её статусом «успех» или «сбой»."""
    оборвать_брошенные(conn, script)
    run_id = conn.execute(
        """INSERT INTO runs.run (script, git_commit, model, note, environment)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (script, git_commit(), model, note, json.dumps({"роль": conn.info.user})),
    ).fetchone()[0]
    conn.commit()
    try:
        yield run_id
    except BaseException:
        conn.rollback()
        conn.execute(
            "UPDATE runs.run SET status = 'сбой', finished_at = now() WHERE id = %s",
            (run_id,),
        )
        conn.commit()
        raise
    else:
        conn.execute(
            "UPDATE runs.run SET status = 'успех', finished_at = now() WHERE id = %s",
            (run_id,),
        )
        conn.commit()


def add_input(conn: psycopg.Connection, run_id: int, path: Path, kind: str = "файл") -> None:
    """Записать входной файл прогона вместе с его хешем."""
    conn.execute(
        """INSERT INTO runs.run_input (run_id, kind, ref_text, checksum)
           VALUES (%s, %s, %s, %s)""",
        (run_id, kind, db.store_ref(path), checksum(path)),
    )


def add_metric(conn: psycopg.Connection, run_id: int, name: str, value: float) -> None:
    conn.execute(
        "INSERT INTO runs.metric (run_id, name, value) VALUES (%s, %s, %s)",
        (run_id, name, float(value)),
    )


# ------------------------------------------------------------------ территории
def ensure_territory(
    conn: psycopg.Connection,
    code: str,
    title: str | None = None,
    scale: str | None = None,
    is_primary: bool = False,
) -> int:
    row = conn.execute(
        "SELECT id FROM data.territory WHERE code = %s", (code,)
    ).fetchone()
    if row:
        return row[0]
    return conn.execute(
        """INSERT INTO data.territory (code, title, scale, is_primary)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (code, title, scale, is_primary),
    ).fetchone()[0]


# ------------------------------------------------------------- сетка и ячейки
def ensure_grid(
    conn: psycopg.Connection,
    code: str,
    cell_size_m: int,
    territory_id: int | None = None,
    srid: int = GRID_SRID,
) -> int:
    row = conn.execute("SELECT id FROM data.grid WHERE code = %s", (code,)).fetchone()
    if row:
        return row[0]
    return conn.execute(
        """INSERT INTO data.grid (code, cell_size_m, crs, territory_id)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (code, cell_size_m, f"EPSG:{srid}", territory_id),
    ).fetchone()[0]


def load_cells(
    conn: psycopg.Connection,
    grid_id: int,
    frame: pd.DataFrame,
    cell_size_m: int,
    srid: int = GRID_SRID,
) -> int:
    """Завести ячейки сетки по колонкам `row`, `col`, `x`, `y` (центры ячеек).

    Геометрия строится в исходной системе координат и переводится в WGS84,
    чтобы слои разных съёмок сравнивались в одном пространстве.
    """
    have = conn.execute(
        "SELECT count(*) FROM data.cell WHERE grid_id = %s", (grid_id,)
    ).fetchone()[0]
    if have:
        return 0

    conn.execute("DROP TABLE IF EXISTS staging.cell_load")
    conn.execute(
        """CREATE TABLE staging.cell_load (
               col integer, row integer, x double precision, y double precision)"""
    )
    with conn.cursor().copy(
        "COPY staging.cell_load (col, row, x, y) FROM STDIN"
    ) as copy:
        for c, r, x, y in frame[["col", "row", "x", "y"]].itertuples(index=False):
            copy.write_row((int(c), int(r), float(x), float(y)))

    half = cell_size_m / 2
    conn.execute(
        """INSERT INTO data.cell (grid_id, col, row, geom, centroid)
            SELECT %s, col, row,
                   ST_Transform(ST_MakeEnvelope(x - %s, y - %s, x + %s, y + %s, %s), 4326),
                   ST_Transform(ST_SetSRID(ST_Point(x, y), %s), 4326)
              FROM staging.cell_load""",
        (grid_id, half, half, half, half, srid, srid),
    )
    n = conn.execute(
        "SELECT count(*) FROM data.cell WHERE grid_id = %s", (grid_id,)
    ).fetchone()[0]
    conn.execute("DROP TABLE staging.cell_load")
    return n


def cell_index(conn: psycopg.Connection, grid_id: int) -> dict[tuple[int, int], int]:
    """Соответствие (col, row) → id ячейки для быстрой загрузки значений."""
    rows = conn.execute(
        "SELECT col, row, id FROM data.cell WHERE grid_id = %s", (grid_id,)
    ).fetchall()
    return {(c, r): i for c, r, i in rows}


# -------------------------------------------------------------------- признаки
def ensure_feature(
    conn: psycopg.Connection,
    code: str,
    assembly: str,
    definition: dict | None = None,
    run_id: int | None = None,
) -> int:
    """Найти или завести признак.

    Версия привязана к сборке: один и тот же код, собранный по-другому
    (другое сырьё, код или параметры), — это новая версия. Признак из другой
    сборки не делает предыдущую устаревшей, поэтому `is_current` снимается
    только с прошлых версий той же сборки.
    """
    definition = {"сборка": assembly, **(definition or {})}
    row = conn.execute(
        """SELECT id FROM data.feature
            WHERE code = %s AND definition->>'сборка' = %s
            ORDER BY version DESC LIMIT 1""",
        (code, assembly),
    ).fetchone()
    if row:
        return row[0]

    version = conn.execute(
        "SELECT coalesce(max(version), 0) + 1 FROM data.feature WHERE code = %s",
        (code,),
    ).fetchone()[0]
    return conn.execute(
        """INSERT INTO data.feature
               (code, version, group_code, definition, produced_by_run)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (code, version, group_of(code), json.dumps(definition, ensure_ascii=False), run_id),
    ).fetchone()[0]


def load_feature_values(
    conn: psycopg.Connection,
    feature_id: int,
    values: pd.Series,
    ids: np.ndarray,
) -> int:
    """Загрузить значения признака по ячейкам; пропуски не пишем.

    Отсутствие строки и есть пропуск — так видно реальное покрытие, и
    не приходится хранить миллионы NaN.
    """
    have = conn.execute(
        "SELECT 1 FROM data.feature_value WHERE feature_id = %s LIMIT 1", (feature_id,)
    ).fetchone()
    if have:
        return 0

    arr = np.asarray(values, dtype="float64")
    good = np.isfinite(arr)
    n = int(good.sum())
    if not n:
        return 0

    with conn.cursor().copy(
        "COPY data.feature_value (feature_id, cell_id, value) FROM STDIN"
    ) as copy:
        for cell_id, value in zip(ids[good], arr[good]):
            copy.write_row((feature_id, int(cell_id), float(value)))
    return n


# ---------------------------------------------------------------- происхождение
def record_derivation(
    conn: psycopg.Connection,
    run_id: int,
    kind: str,
    source_ref: str,
    feature_id: int,
    params: dict | None = None,
) -> None:
    """Ребро графа: из какого файла какой операцией получен признак."""
    op_id = conn.execute(
        """INSERT INTO meta.operation (kind, script, git_commit, params, run_id)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (
            kind,
            "gisdb/db_ingest.py",
            git_commit(),
            json.dumps(params or {}, ensure_ascii=False),
            run_id,
        ),
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO meta.derivation
               (operation_id, source_kind, source_ref, target_kind, target_id)
           VALUES (%s, 'файл', %s, 'признак', %s)""",
        (op_id, db.store_ref(source_ref), feature_id),
    )


# ------------------------------------------------------------ загрузка датасета
def load_dataset(
    conn: psycopg.Connection,
    run_id: int,
    path: Path,
    grid_code: str,
    cell_size_m: int,
    territory_code: str,
    territory_title: str | None = None,
    is_primary: bool = False,
    skip: tuple[str, ...] = (),
    progress: bool = True,
) -> dict[str, int]:
    """Перенести датасет-таблицу (row, col, x, y + признаки) в базу целиком."""
    frame = pd.read_parquet(path)
    add_input(conn, run_id, path)

    territory_id = ensure_territory(
        conn, territory_code, territory_title, "1:200 000", is_primary
    )
    grid_id = ensure_grid(conn, grid_code, cell_size_m, territory_id)
    n_cells = load_cells(conn, grid_id, frame, cell_size_m)
    conn.commit()

    index = cell_index(conn, grid_id)
    ids = np.array(
        [index[(int(c), int(r))] for c, r in zip(frame["col"], frame["row"])],
        dtype="int64",
    )

    columns = [
        c for c in frame.columns
        if c not in ADDRESS_COLUMNS and c not in skip
        and pd.api.types.is_numeric_dtype(frame[c])
    ]
    n_values = 0
    for i, column in enumerate(columns, 1):
        feature_id = ensure_feature(
            conn, column, assembly=path.stem,
            definition={"файл": db.store_ref(path), "колонка": column, "сетка": grid_code},
            run_id=run_id,
        )
        written = load_feature_values(conn, feature_id, frame[column], ids)
        if written:
            record_derivation(
                conn, run_id, "перенос в базу", str(path), feature_id,
                {"колонка": column, "сетка": grid_code},
            )
        n_values += written
        conn.commit()
        if progress:
            print(f"  [{i}/{len(columns)}] {column}: {written} значений", flush=True)

    add_metric(conn, run_id, f"ячеек:{grid_code}", n_cells)
    add_metric(conn, run_id, f"значений:{grid_code}", n_values)
    conn.commit()
    return {"ячеек": n_cells, "признаков": len(columns), "значений": n_values}
