"""Снимок словаря признаков из проекта прогноза в базу.

Единственное место, где базе нужен репозиторий прогноза по признакам: здесь
читается `src/feature_dict.py` и его вывод для кодов базы ложится в
`meta.feature_term` (миграция 016). После этого ядро — панель, сбор фактов,
паспортизатор — работает без проекта вообще.

Что переносится на каждый код:

* группа (`classify`) — 'ast', 'pf', 'geo2'…;
* техническое описание (`describe`) — что это за столбец, из какого источника,
  какой канал, как свёрнут;
* эталонная предметная формулировка (`geo_note`), если словарь её даёт. Это
  формулировка эксперта из проекта; в паспорт она не попадает и подтверждения
  не требует — она нужна, чтобы сверять с ней вывод модели.

Словарь остаётся единственным — в проекте. Здесь только снимок, и у него есть
отпечаток источника: `--проверить` говорит, отстал ли снимок от файла.

    python scripts/db_load_dictionary.py --проверить    сверить отпечаток
    python scripts/db_load_dictionary.py --сухой        показать, не записывая
    python scripts/db_load_dictionary.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gisdb import db  # noqa: E402
from gisdb import db_ingest as ingest  # noqa: E402
from gisdb.project import подключить  # noqa: E402

СКРИПТ = "scripts/db_load_dictionary.py"
СЛОВАРЬ = Path("src/feature_dict.py")

# Начало фразы, которой словарь сообщает, что формулировки у него нет. Такую
# строку в базу не пишем: пустое поле честнее отговорки, и по нему видно, где
# предметного смысла в проекте ещё не написано.
НЕТ_ФОРМУЛИРОВКИ = "прямой геологической интерпретации не установлено"


def коды_базы(conn) -> dict[str, str | None]:
    """Код признака → группа, записанная в базе (может быть пустой)."""
    строки = conn.execute(
        "SELECT DISTINCT code, group_code FROM data.feature ORDER BY code"
    ).fetchall()
    return {код: группа for код, группа in строки}


def разобрать(коды: dict[str, str | None]) -> list[tuple[str, str, str, str | None]]:
    """Прогнать коды через словарь проекта: группа, описание, формулировка."""
    from src.feature_dict import classify, describe, geo_note      # из проекта

    готово = []
    for код, группа_базы in коды.items():
        группа = группа_базы or classify(код)
        # 'dem' в базе и 'ter' в словаре — одна и та же группа рельефа; так же
        # это делает сбор фактов, и расхождение здесь дало бы другое описание.
        описание = describe(код, "ter" if группа == "dem" else группа)
        формулировка = geo_note(код, группа)
        if формулировка.startswith(НЕТ_ФОРМУЛИРОВКИ):
            формулировка = None
        готово.append((код, группа, описание, формулировка))
    return готово


def записать(conn, run_id: int, ряды, ссылка: str, отпечаток: str) -> int:
    conn.executemany(
        """INSERT INTO meta.feature_term
               (code, group_code, technical, geo_note, source_ref, source_hash, run_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (code) DO UPDATE SET
               group_code = EXCLUDED.group_code, technical = EXCLUDED.technical,
               geo_note = EXCLUDED.geo_note, source_ref = EXCLUDED.source_ref,
               source_hash = EXCLUDED.source_hash, run_id = EXCLUDED.run_id,
               loaded_at = now()""",
        [(код, группа, описание, формулировка, ссылка, отпечаток, run_id)
         for код, группа, описание, формулировка in ряды],
    )
    return len(ряды)


def сверить(conn, путь: Path) -> int:
    """Сравнить отпечаток снимка с файлом словаря; вернуть код возврата."""
    строки = conn.execute(
        """SELECT source_hash, count(*), max(loaded_at)
             FROM meta.feature_term GROUP BY source_hash ORDER BY 2 DESC"""
    ).fetchall()
    пробелы = conn.execute("SELECT count(*) FROM meta.feature_term_gap").fetchone()[0]
    if not строки:
        print("снимка словаря в базе нет: запустите без ключей")
        return 1
    сейчас = ingest.checksum(путь) if путь.is_file() else None
    print(f"кодов в снимке: {sum(с[1] for с in строки)}; "
          f"кодов базы без описания: {пробелы}")
    устарело = 0
    for отпечаток, сколько, когда in строки:
        свежий = отпечаток == сейчас
        устарело += 0 if свежий else сколько
        print(f"  {отпечаток[:12]}  {сколько:4} кодов  "
              f"{когда:%d.%m.%Y %H:%M}  {'свежий' if свежий else 'ОТСТАЛ'}")
    if сейчас is None:
        print(f"файл словаря недоступен ({путь}) — сверить не с чем")
        return 1
    if устарело:
        print(f"\nсловарь проекта изменился после загрузки: {устарело} кодов "
              "описаны по прежней версии. Обновить: python " + СКРИПТ)
    return 1 if устарело else 0


def main(аргументы: list[str] | None = None) -> int:
    разбор = argparse.ArgumentParser(description="Снимок словаря признаков в базу")
    разбор.add_argument("--сухой", action="store_true", help="показать, не записывая")
    разбор.add_argument("--проверить", action="store_true",
                        help="сверить отпечаток снимка с файлом словаря")
    ключи = разбор.parse_args(аргументы)

    if ключи.проверить:
        # Сверка смотрит только в базу: проекта рядом может и не быть — именно
        # для этого снимок и делается.
        путь = db.PROJECT_ROOT.parent / "GIS" / СЛОВАРЬ
        try:
            путь = подключить() / СЛОВАРЬ
        except RuntimeError:
            pass
        with db.connect("gis_read") as conn:
            return сверить(conn, путь)

    корень = подключить()
    путь = корень / СЛОВАРЬ
    if not путь.is_file():
        raise SystemExit(f"словаря нет: {путь}")

    with db.connect("gis_agent") as conn:
        коды = коды_базы(conn)
        ряды = разобрать(коды)
        с_формулировкой = sum(1 for *_, ф in ряды if ф)
        print(f"кодов признаков в базе: {len(коды)}")
        print(f"описание из словаря получили: {len(ряды)}")
        print(f"из них с предметной формулировкой эксперта: {с_формулировкой}; "
              f"без неё: {len(ряды) - с_формулировкой}")
        if ключи.сухой:
            for код, группа, описание, формулировка in ряды[:3]:
                print(f"\n  {код} (группа {группа})\n    {описание[:160]}")
                print(f"    эталон: {(формулировка or '—')[:160]}")
            print("\nсухой прогон: ничего не записано")
            return 0

        ссылка, отпечаток = db.store_ref(путь), ingest.checksum(путь)
        with ingest.run(conn, script=СКРИПТ,
                        note=f"словарь признаков: {len(ряды)} кодов") as run_id:
            ingest.add_input(conn, run_id, путь, kind="словарь")
            записано = записать(conn, run_id, ряды, ссылка, отпечаток)
            ingest.add_metric(conn, run_id, "кодов описано", записано)
            ingest.add_metric(conn, run_id, "с формулировкой эксперта", с_формулировкой)
            conn.commit()
        пробелы = conn.execute("SELECT count(*) FROM meta.feature_term_gap").fetchone()[0]
        print(f"\nзаписано кодов: {записано}; прогон #{run_id}; "
              f"кодов базы без описания: {пробелы}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
