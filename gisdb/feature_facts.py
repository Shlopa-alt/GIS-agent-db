"""Факты о признаке из базы — общий вход для агента и для панели подтверждений.

Модель заполняет паспорт признака по фактам: откуда слой, как получен, как
свёрнут в ячейку, какое покрытие, какая статистика значений. Человек в панели
проверяет её вывод — и должен видеть ровно те же факты, иначе проверка
превращается в оценку красоты текста.

Поэтому сбор фактов и формулировка задачи живут здесь, а не внутри агента:
`scripts/db_agent_passporter.py` отправляет их модели, `gisdb/panel` показывает
человеку.
"""

from __future__ import annotations

from gisdb.project import подключить

подключить()                       # словарь признаков живёт в проекте
from src.feature_dict import classify, describe      # noqa: E402

# Столбцы выборки в том порядке, в каком идут в запросе.
СТОЛБЦЫ: tuple[str, ...] = (
    "id", "code", "group_code", "сборка", "сетка", "территория",
    "coverage_frac", "missing_frac", "circularity", "transferability",
    "footprint_dependent", "aggregation_rule", "geological_meaning",
    "kb_reference", "model", "status", "importance_checked",
)

ЗАПРОС = """
SELECT f.id, f.code, f.group_code, f.definition->>'сборка' AS сборка,
       g.code AS сетка, t.title AS территория,
       p.coverage_frac, p.missing_frac, p.circularity, p.transferability,
       p.footprint_dependent, p.aggregation_rule, p.geological_meaning,
       p.kb_reference, p.model, p.status, p.importance_checked
  FROM meta.feature_passport p
  JOIN data.feature f ON f.id = p.feature_id
  LEFT JOIN data.grid g ON g.code = f.definition->>'сетка'
  LEFT JOIN data.territory t ON t.id = g.territory_id
 {где}
 ORDER BY f.code, f.id
"""

ИСТОЧНИКИ = """
SELECT DISTINCT l.title, l.provider, l.resolution_m, l.format, lp.method
  FROM meta.derivation d
  JOIN data.source_layer l ON l.id = d.source_id
  LEFT JOIN meta.layer_passport lp ON lp.layer_id = l.id
 WHERE d.source_kind = 'слой' AND d.target_kind = 'признак' AND d.target_id = %s
"""

СТАТИСТИКА = """
SELECT count(*), min(value), avg(value), max(value), stddev_samp(value)
  FROM data.feature_value WHERE feature_id = %s
"""


def выбрать(conn, где: str = "", параметры: tuple = ()) -> list[dict]:
    """Паспорта признаков с условием отбора; `где` — готовое предложение WHERE."""
    строки = conn.execute(ЗАПРОС.format(где=где), параметры).fetchall()
    return [dict(zip(СТОЛБЦЫ, с)) for с in строки]


def факты(conn, строка: dict) -> dict:
    """Собрать всё, что база знает о признаке, — вход задачи для модели."""
    источники = conn.execute(ИСТОЧНИКИ, (строка["id"],)).fetchall()
    n, мин, среднее, макс, ско = conn.execute(СТАТИСТИКА, (строка["id"],)).fetchone()
    группа = строка["group_code"] or classify(строка["code"])
    return {
        **строка,
        "группа": группа,
        "техническое": describe(строка["code"], "ter" if группа == "dem" else группа),
        "источники": [
            {"слой": t, "поставщик": p, "разрешение_м": r, "формат": ф, "метод": м}
            for t, p, r, ф, м in источники
        ],
        "значений": n, "мин": мин, "среднее": среднее, "макс": макс, "ско": ско,
    }


def задача(ф: dict) -> str:
    """Текст задачи: только факты, без готовых предметных формулировок проекта."""
    строки = [
        f"Признак базы: {ф['code']} (группа {ф['группа']}).",
        f"Техническое описание из словаря проекта: {ф['техническое']}.",
    ]
    for и in ф["источники"]:
        разрешение = f", разрешение {и['разрешение_м']:.0f} м" if и["разрешение_м"] else ""
        строки.append(f"Источник: {и['слой']} ({и['поставщик']}{разрешение}).")
        if и["метод"]:
            строки.append(f"Как получен источник: {и['метод']}")
    if not ф["источники"]:
        строки.append("Слой-источник в базе не указан: признак получен расчётом внутри проекта.")

    строки += [
        f"Территория: {ф['территория'] or 'не указана'}, сетка {ф['сетка'] or 'не указана'}.",
        f"Свёртка в ячейку: {ф['aggregation_rule'] or 'не указана'}.",
        f"Покрытие территории: {ф['coverage_frac']:.3f} "
        f"(пропусков {ф['missing_frac']:.3f}); "
        f"зависит от контура съёмки: {'да' if ф['footprint_dependent'] else 'нет'}.",
        f"Входит в критериальную формулу ВНИГНИ (риск замкнутого вывода): "
        f"{'да' if ф['circularity'] else 'нет'}.",
        f"Переносимость на смежную территорию: {ф['transferability']}.",
    ]
    if ф["значений"]:
        строки.append(
            f"Значения в базе: {ф['значений']} ячеек, минимум {ф['мин']:.4g}, "
            f"среднее {ф['среднее']:.4g}, максимум {ф['макс']:.4g}, "
            f"стандартное отклонение {ф['ско']:.4g}."
        )
    строки.append(
        "Задача: заполнить паспорт этого признака — что он значит геологически "
        "для прогноза золото-урановых рудных узлов."
    )
    return "\n".join(строки)
