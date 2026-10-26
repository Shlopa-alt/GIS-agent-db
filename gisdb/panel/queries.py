"""Запросы панели подтверждений: очередь, карточка, решение человека.

Разделение простое: здесь всё, что говорит с базой, в `__init__.py` — только
маршруты и разметка. Так правило «подтверждает человек» проверяется чтением
одного файла.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from gisdb import feature_facts


@dataclass(frozen=True)
class Вид:
    """Очередь одного рода объектов: где лежит, что человек вправе править."""

    ключ: str          # в адресе страницы
    название: str      # как записывается в meta.review.object_kind
    заголовок: str     # как показывается человеку
    таблица: str
    первичный: str     # столбец первичного ключа
    поля: tuple[str, ...]  # столбцы, открытые для правки


ВИДЫ: dict[str, Вид] = {
    "признаки": Вид(
        "признаки", "паспорт признака", "Паспорта признаков",
        "meta.feature_passport", "feature_id",
        ("geological_meaning", "kb_reference"),
    ),
    "слои": Вид(
        "слои", "паспорт слоя", "Паспорта слоёв-источников",
        "meta.layer_passport", "layer_id",
        ("source_note", "method", "footprint_note", "license_id"),
    ),
    "лицензии": Вид(
        "лицензии", "лицензия", "Лицензии",
        "meta.license", "id",
        ("title", "url", "allows_redistribution"),
    ),
    "связи": Вид(
        "связи", "связь признак-понятие", "Связи признаков с понятиями",
        "bridge.feature_concept", "id",
        ("concept_code", "role", "note"),
    ),
    # Понятия стоят в очереди раньше связей по смыслу: определение решает,
    # какие связи вообще имеют право быть.
    #
    # `parent_code` правится здесь, а не ставится загрузчиком: место понятия в
    # дереве предметной модели — утверждение о самой модели. Поставить его
    # миграцией значило бы изменить содержание понятия, оставив под ним
    # прежнюю подпись.
    "понятия": Вид(
        "понятия", "понятие", "Понятия базы знаний",
        "kb.concept", "id",
        ("definition", "source", "note", "parent_code"),
    ),
    # Цитаты правятся не здесь: текст цитаты — дословно из статьи, менять его
    # нельзя, можно только принять или отклонить. Поэтому полей для правки нет.
    # Решение выносится прямо в карточке понятия, рядом с его определением;
    # эта очередь нужна, чтобы черновые цитаты были видны и считались.
    "цитаты": Вид(
        "цитаты", "цитата", "Цитаты из статей",
        "kb.citation", "id",
        (),
    ),
}

# Человеческие подписи полей: в форме правки и в журнале решений.
ПОДПИСИ: dict[str, str] = {
    "geological_meaning": "Геологический смысл",
    "kb_reference": "Обоснование",
    "source_note": "Откуда взят",
    "method": "Как получен и приведён",
    "footprint_note": "Чем контур съёмки может сместить выборку",
    "license_id": "Лицензия",
    "title": "Название",
    "url": "Ссылка",
    "allows_redistribution": "Разрешает распространение производных",
    "concept_code": "Понятие",
    "role": "Роль признака",
    "note": "Примечание",
    "definition": "Определение",
    "source": "Откуда определение",
    "kind": "Род понятия",
    "parent_code": "Понятие уровнем выше",
}

СТАТУСЫ = ("черновик", "подтверждён", "отклонён")


# --------------------------------------------------------------------- сводка
def сводка(conn) -> list[dict]:
    """Сколько черновиков, подтверждённых и отклонённых в каждой очереди."""
    строки = conn.execute(
        "SELECT вид, status, count(*) FROM meta.confirmation_queue GROUP BY 1, 2"
    ).fetchall()
    счёт: dict[str, dict[str, int]] = {}
    for вид, статус, сколько in строки:
        счёт.setdefault(вид, {}).setdefault(статус, 0)
        счёт[вид][статус] += сколько
    итог = []
    for вид in ВИДЫ.values():
        по_статусам = счёт.get(вид.название, {})
        итог.append({
            "вид": вид,
            "черновик": по_статусам.get("черновик", 0),
            "подтверждён": по_статусам.get("подтверждён", 0),
            "отклонён": по_статусам.get("отклонён", 0),
            "всего": sum(по_статусам.values()),
        })
    return итог


# -------------------------------------------------------------------- очереди
СПИСОК_ПРИЗНАКОВ = """
SELECT min(p.feature_id) AS id, f.code, f.group_code, count(*) AS паспортов,
       max(p.geological_meaning) AS текст, max(p.model) AS model,
       max(p.coverage_frac) AS покрытие, bool_or(p.circularity) AS формула
  FROM meta.feature_passport p
  JOIN data.feature f ON f.id = p.feature_id
 WHERE p.status = %s
 GROUP BY f.code, f.group_code
 ORDER BY {порядок}
"""

СПИСОК_СЛОЁВ = """
SELECT p.layer_id AS id, l.code, l.kind AS group_code, 1 AS паспортов,
       coalesce(p.method, p.source_note) AS текст, p.model,
       p.coverage_frac AS покрытие, false AS формула
  FROM meta.layer_passport p
  JOIN data.source_layer l ON l.id = p.layer_id
 WHERE p.status = %s
 ORDER BY {порядок}
"""

СПИСОК_ЛИЦЕНЗИЙ = """
SELECT c.id, c.code, NULL AS group_code, count(p.layer_id) AS паспортов,
       c.title AS текст, NULL AS model, NULL AS покрытие, false AS формула
  FROM meta.license c
  LEFT JOIN meta.layer_passport p ON p.license_id = c.id
 WHERE c.status = %s
 GROUP BY c.id, c.code, c.title
 ORDER BY {порядок}
"""

СПИСОК_СВЯЗЕЙ = """
SELECT b.id, f.code || ' → ' || b.concept_code AS code, b.role AS group_code,
       1 AS паспортов, b.note AS текст, r.model, NULL AS покрытие, false AS формула
  FROM bridge.feature_concept b
  JOIN data.feature f ON f.id = b.feature_id
  LEFT JOIN runs.run r ON r.id = b.run_id
 WHERE b.status = %s
 ORDER BY {порядок}
"""

СПИСОК_ПОНЯТИЙ = """
SELECT c.id, c.code, c.kind AS group_code, count(b.id) AS паспортов,
       c.definition AS текст, NULL AS model, NULL AS покрытие, false AS формула
  FROM kb.concept c
  LEFT JOIN bridge.feature_concept b ON b.concept_code = c.code
 WHERE c.status = %s
 GROUP BY c.id, c.code, c.kind, c.definition
 ORDER BY {порядок}
"""

# Цитаты в очереди сгруппированы по понятию только на вид: строка — это цитата,
# но в столбце кода стоит понятие, под которое она пришла. Так в списке видно,
# где литературы много, а где её нет вовсе.
СПИСОК_ЦИТАТ = """
SELECT ц.id, ц.concept_code AS code, ц.kb_found_by AS group_code, 1 AS паспортов,
       left(ц.quote, 300) AS текст, NULL AS model, ц.kb_similarity AS покрытие,
       false AS формула
  FROM kb.citation ц
 WHERE ц.status = %s
 ORDER BY {порядок}
"""

СПИСКИ = {
    "признаки": СПИСОК_ПРИЗНАКОВ, "слои": СПИСОК_СЛОЁВ,
    "лицензии": СПИСОК_ЛИЦЕНЗИЙ, "связи": СПИСОК_СВЯЗЕЙ,
    "понятия": СПИСОК_ПОНЯТИЙ, "цитаты": СПИСОК_ЦИТАТ,
}
СТОЛБЦЫ_СПИСКА = ("id", "код", "группа", "паспортов", "текст",
                  "модель", "покрытие", "формула")

#: Порядок строк в очереди. Ключ — то, что человек нажимает в заголовке
#: столбца; значение — выражение для ORDER BY. Подставляется в запрос, поэтому
#: берётся только отсюда: ни одно значение из адреса в SQL не попадает.
#: Первый ключ каждого вида — порядок по умолчанию.
#:
#: Зачем это вообще. В очереди признаков 149 кодов, и разбирать их приходится
#: не подряд: то по группе съёмки, то по исполнителю (41 паспорт описан внешней
#: моделью и 6 — средством разработки, их надо найти и вернуть на доработку),
#: то по покрытию. Без сортировки единственный способ — искать глазами.
СОРТИРОВКИ: dict[str, dict[str, str]] = {
    "признаки": {
        "группа": "f.group_code, f.code",
        "код": "f.code",
        "исполнитель": "max(p.model), f.code",
        "покрытие": "max(p.coverage_frac) DESC NULLS LAST, f.code",
        "сборок": "count(*) DESC, f.code",
        "в формуле": "bool_or(p.circularity) DESC, f.group_code, f.code",
    },
    "слои": {
        "код": "l.code",
        "вид": "l.kind, l.code",
        "покрытие": "p.coverage_frac DESC NULLS LAST, l.code",
    },
    "лицензии": {"код": "c.code", "слоёв": "count(p.layer_id) DESC, c.code"},
    "связи": {
        "признак": "f.code, b.concept_code",
        "понятие": "b.concept_code, f.code",
        "роль": "b.role, f.code",
    },
    "понятия": {
        "уровень": "c.kind, c.code",
        "код": "c.code",
        "признаков": "count(b.id) DESC, c.code",
    },
    "цитаты": {
        "понятие": "ц.concept_code, ц.kb_similarity DESC NULLS LAST, ц.id",
        "близость": "ц.kb_similarity DESC NULLS LAST, ц.id",
        "чем нашлось": "ц.kb_found_by, ц.id",
    },
}


def порядок(вид: Вид, сорт: str | None) -> tuple[str, str]:
    """Выражение сортировки и её имя. Неизвестное имя молча не подставляем —
    берём умолчание вида: в ORDER BY уходит только то, что перечислено выше."""
    варианты = СОРТИРОВКИ[вид.ключ]
    имя = сорт if сорт in варианты else next(iter(варианты))
    return варианты[имя], имя


def очередь(conn, вид: Вид, статус: str = "черновик",
            сорт: str | None = None) -> list[dict]:
    """Список объектов очереди. Признаки сведены по коду: смысл от сборки не зависит."""
    выражение, _ = порядок(вид, сорт)
    строки = conn.execute(
        СПИСКИ[вид.ключ].format(порядок=выражение), (статус,)).fetchall()
    return [dict(zip(СТОЛБЦЫ_СПИСКА, с)) for с in строки]


def сосед(conn, вид: Вид, после: int | None = None, статус: str = "черновик",
          шаг: int = 1, сорт: str | None = None) -> int | None:
    """Соседний объект очереди в том же порядке, в каком она показана.

    Статус — тот, который человек сейчас разбирает: по подтверждённым тоже
    ходят подряд (ищут, что вернуть на доработку), и раньше перехода там не
    было вовсе — приходилось возвращаться в список после каждой карточки.
    """
    номера = [з["id"] for з in очередь(conn, вид, статус, сорт)]
    if not номера:
        return None
    if после not in номера:
        return номера[0]
    место = номера.index(после) + шаг
    return номера[место] if 0 <= место < len(номера) else None


def следующий(conn, вид: Вид, после: int | None = None,
              статус: str = "черновик", сорт: str | None = None) -> int | None:
    """Следующий объект очереди; дойдя до конца — первый, чтобы идти по кругу."""
    дальше = сосед(conn, вид, после, статус, 1, сорт)
    if дальше is not None:
        return дальше
    номера = [з["id"] for з in очередь(conn, вид, статус, сорт)]
    хвост = [н for н in номера if н != после]
    return хвост[0] if хвост else None


# ------------------------------------------------------------------- карточки
def карточка_признака(conn, feature_id: int) -> dict:
    записи = feature_facts.выбрать(conn, "WHERE f.id = %s", (feature_id,))
    if not записи:
        raise KeyError(feature_id)
    ф = feature_facts.факты(conn, записи[0])
    ф["задача"] = feature_facts.задача(ф)
    ф["сборки"] = conn.execute(
        """SELECT f.id, f.definition->>'сборка', p.status
             FROM data.feature f
             JOIN meta.feature_passport p ON p.feature_id = f.id
            WHERE f.code = %s ORDER BY f.id""",
        (ф["code"],),
    ).fetchall()
    ф["черновиков_с_кодом"] = sum(1 for _, _, с in ф["сборки"] if с == "черновик")
    ф["понятия"] = conn.execute(
        """SELECT concept_code, role, status FROM bridge.feature_concept
            WHERE feature_id = %s""",
        (feature_id,),
    ).fetchall()
    # Машинные проверки (meta.feature_check): это факты о расчётах, а не
    # суждения о признаке, поэтому человек их не подтверждает — только читает
    # рядом с тем, что подтверждает.
    ф["проверки"] = conn.execute(
        """SELECT kind, verdict, value, source_ref, note, checked_at
             FROM meta.feature_check WHERE feature_id = %s ORDER BY kind""",
        (feature_id,),
    ).fetchall()
    ф["заголовок"] = ф["code"]
    ф["значения_полей"] = {
        "geological_meaning": ф["geological_meaning"],
        "kb_reference": ф["kb_reference"],
    }
    return ф


def карточка_слоя(conn, layer_id: int) -> dict:
    строка = conn.execute(
        """SELECT l.code, l.title, l.kind, l.provider, l.url, l.path, l.format,
                  l.crs, l.resolution_m, l.acquired_from, l.acquired_to,
                  l.size_bytes, l.checksum, l.evicted,
                  p.source_note, p.method, p.footprint_note, p.coverage_frac,
                  p.missing_frac, p.license_id, p.model, p.status
             FROM meta.layer_passport p
             JOIN data.source_layer l ON l.id = p.layer_id
            WHERE p.layer_id = %s""",
        (layer_id,),
    ).fetchone()
    if строка is None:
        raise KeyError(layer_id)
    имена = ("code", "title", "kind", "provider", "url", "path", "format", "crs",
             "resolution_m", "acquired_from", "acquired_to", "size_bytes",
             "checksum", "evicted", "source_note", "method", "footprint_note",
             "coverage_frac", "missing_frac", "license_id", "model", "status")
    к = dict(zip(имена, строка))
    к["id"] = layer_id
    к["заголовок"] = к["code"]
    к["признаков"] = conn.execute(
        """SELECT count(DISTINCT target_id) FROM meta.derivation
            WHERE source_kind = 'слой' AND source_id = %s AND target_kind = 'признак'""",
        (layer_id,),
    ).fetchone()[0]
    к["лицензии"] = conn.execute(
        "SELECT id, code, title, status FROM meta.license ORDER BY code"
    ).fetchall()
    к["значения_полей"] = {п: к[п] for п in ВИДЫ["слои"].поля}
    return к


def карточка_лицензии(conn, license_id: int) -> dict:
    строка = conn.execute(
        """SELECT code, title, url, allows_redistribution, status
             FROM meta.license WHERE id = %s""",
        (license_id,),
    ).fetchone()
    if строка is None:
        raise KeyError(license_id)
    к = dict(zip(("code", "title", "url", "allows_redistribution", "status"), строка))
    к["id"] = license_id
    к["заголовок"] = к["code"]
    # Что зависит от решения: подтверждая лицензию, человек отвечает за эти слои.
    к["слои"] = conn.execute(
        """SELECT l.code, l.title, p.status
             FROM meta.layer_passport p
             JOIN data.source_layer l ON l.id = p.layer_id
            WHERE p.license_id = %s ORDER BY l.code""",
        (license_id,),
    ).fetchall()
    к["признаков"] = conn.execute(
        """SELECT count(DISTINCT d.target_id)
             FROM meta.layer_passport p
             JOIN meta.derivation d ON d.source_kind = 'слой' AND d.source_id = p.layer_id
            WHERE p.license_id = %s AND d.target_kind = 'признак'""",
        (license_id,),
    ).fetchone()[0]
    к["значения_полей"] = {п: к[п] for п in ВИДЫ["лицензии"].поля}
    return к


def карточка_связи(conn, link_id: int) -> dict:
    строка = conn.execute(
        """SELECT b.feature_id, f.code, b.concept_code, b.role, b.origin,
                  b.evidence_ref, b.note, b.status, r.model, p.geological_meaning
             FROM bridge.feature_concept b
             JOIN data.feature f ON f.id = b.feature_id
             LEFT JOIN runs.run r ON r.id = b.run_id
             LEFT JOIN meta.feature_passport p ON p.feature_id = b.feature_id
            WHERE b.id = %s""",
        (link_id,),
    ).fetchone()
    if строка is None:
        raise KeyError(link_id)
    имена = ("feature_id", "code", "concept_code", "role", "origin",
             "evidence_ref", "note", "status", "model", "geological_meaning")
    к = dict(zip(имена, строка))
    к["id"] = link_id
    к["заголовок"] = f"{к['code']} → {к['concept_code']}"
    к["значения_полей"] = {п: к[п] for п in ВИДЫ["связи"].поля}
    return к


#: Что может стоять уровнем выше у понятия этого рода (миграция 018). Пустой
#: список — верхний уровень, у него понятия выше не бывает.
ВЫШЕ_ПО_РОДУ: dict[str, tuple[str, ...]] = {
    "этап процесса": (),
    "рудоконтролирующий фактор": ("этап процесса",),
    "условие наблюдения": ("этап процесса",),
    "критерий": ("рудоконтролирующий фактор", "условие наблюдения"),
}


def карточка_понятия(conn, concept_id: int) -> dict:
    строка = conn.execute(
        """SELECT code, kind, definition, source, note, status, parent_code
             FROM kb.concept WHERE id = %s""",
        (concept_id,),
    ).fetchone()
    if строка is None:
        raise KeyError(concept_id)
    имена = ("code", "kind", "definition", "source", "note", "status", "parent_code")
    к = dict(zip(имена, строка))
    к["id"] = concept_id
    к["заголовок"] = к["code"]

    # Выбор, а не ввод текстом: порядок уровней всё равно держит база, но
    # отказом после отправки формы. Список допустимых родителей показывает
    # правило до того, как человек в него упрётся.
    роды = ВЫШЕ_ПО_РОДУ.get(к["kind"], ())
    к["возможные_выше"] = [код for (код,) in conn.execute(
        """SELECT code FROM kb.concept
            WHERE kind = ANY(%s) AND status <> 'отклонён' AND code <> %s
            ORDER BY code""",
        (list(роды), к["code"]),
    ).fetchall()] if роды else []
    # Где понятие стоит сейчас — от этапа вниз. Нужно затем же, зачем список
    # связей: решение о месте в дереве принимается, видя дерево.
    дерево = conn.execute(
        "SELECT этап, фактор, критерий FROM kb.concept_tree WHERE понятие = %s",
        (к["code"],),
    ).fetchone()
    к["этап"], к["фактор"], к["критерий"] = дерево or (None, None, None)
    # Что зависит от решения: связи, опирающиеся на это понятие. Их бывает
    # много, поэтому в карточке — счёт и первые признаки, а не весь список.
    к["связей"] = conn.execute(
        "SELECT count(*) FROM bridge.feature_concept WHERE concept_code = %s",
        (к["code"],),
    ).fetchone()[0]
    к["признаки"] = conn.execute(
        """SELECT f.code, b.role, b.status
             FROM bridge.feature_concept b
             JOIN data.feature f ON f.id = b.feature_id
            WHERE b.concept_code = %s
            ORDER BY f.code LIMIT 20""",
        (к["code"],),
    ).fetchall()
    к["цитаты"] = цитаты_понятия(conn, к["code"])
    к["значения_полей"] = {п: к[п] for п in ВИДЫ["понятия"].поля}
    return к


ЦИТАТЫ = """
SELECT ц.id, ц.status::text, ц.quote, ц.page, ц.territory, ц.method,
       ц.kb_similarity, ц.kb_found_by, ц.kb_verdict, ц.concept_code,
       п.title, п.year, п.url, п.doi, п.authors
  FROM kb.citation ц
  JOIN kb.publication п ON п.id = ц.publication_id
 {где}
 ORDER BY (ц.status = 'черновик') DESC, ц.kb_similarity DESC NULLS LAST, ц.id
"""

СТОЛБЦЫ_ЦИТАТЫ = ("id", "status", "quote", "page", "territory", "method",
                  "kb_similarity", "kb_found_by", "kb_verdict", "concept_code",
                  "title", "year", "url", "doi", "authors")


def _цитата(строка) -> dict:
    ц = dict(zip(СТОЛБЦЫ_ЦИТАТЫ, строка))
    авторы = ц.get("authors") or []
    кто = (авторы[0] + (" и др." if len(авторы) > 1 else "")) if авторы else ""
    год = f", {ц['year']}" if ц.get("year") else ""
    ц["статья"] = f"{кто} {ц.get('title') or ''}{год}".strip()
    ц["ссылка"] = f"https://doi.org/{ц['doi']}" if ц.get("doi") else (ц.get("url") or "")
    # Чем уже был отбор, тем ближе цитата к нашей территории, а не «вообще к
    # золоту»: человеку это важнее числа близости.
    ц["отбор"] = ", ".join(x for x in (ц.get("territory"), ц.get("method")) if x) \
        or "без отбора по территории и методу"
    return ц


def цитаты_понятия(conn, код: str) -> list[dict]:
    """Литература под понятие: сперва черновики, внутри — по близости."""
    строки = conn.execute(ЦИТАТЫ.format(где="WHERE ц.concept_code = %s"), (код,)).fetchall()
    return [_цитата(с) for с in строки]


def карточка_цитаты(conn, citation_id: int) -> dict:
    """Одна цитата. Главный путь решения — карточка понятия; эта страница нужна
    для ссылок из очереди и для журнала решений."""
    строки = conn.execute(ЦИТАТЫ.format(где="WHERE ц.id = %s"), (citation_id,)).fetchall()
    if not строки:
        raise KeyError(citation_id)
    к = _цитата(строки[0])
    к["заголовок"] = f"{к['concept_code']} ← {(к.get('title') or '')[:60]}"
    к["понятие_id"] = conn.execute(
        "SELECT id FROM kb.concept WHERE code = %s", (к["concept_code"],)
    ).fetchone()[0]
    к["значения_полей"] = {}
    return к


КАРТОЧКИ = {
    "признаки": карточка_признака, "слои": карточка_слоя,
    "лицензии": карточка_лицензии, "связи": карточка_связи,
    "понятия": карточка_понятия, "цитаты": карточка_цитаты,
}


def карточка(conn, вид: Вид, номер: int) -> dict:
    к = КАРТОЧКИ[вид.ключ](conn, номер)
    к["вид"] = вид
    к["решения"] = conn.execute(
        """SELECT at, reviewer, decision, comment FROM meta.review
            WHERE object_kind = %s AND object_id = %s ORDER BY at DESC""",
        (вид.название, номер),
    ).fetchall()
    return к


# -------------------------------------------------------------------- решение
def _правки(текущее: dict, новое: dict) -> dict:
    """Что человек действительно изменил: поле -> было/стало.

    Сравниваются уже приведённые значения, а не строки формы: иначе снятая
    галочка и пустое поле выглядят правкой там, где ничего не менялось.
    """
    изменения = {}
    for поле, значение in новое.items():
        было = текущее.get(поле)
        пусто = было in (None, "") and значение in (None, "")
        if значение != было and not пусто:
            изменения[ПОДПИСИ.get(поле, поле)] = {"было": было, "стало": значение}
    return изменения


def _приведение(вид: Вид, поле: str, значение: str):
    """Значение из формы в тип столбца: галочка, номер лицензии, текст."""
    if поле == "allows_redistribution":
        return значение in ("да", "on", "true", "True")
    if поле == "license_id":
        return int(значение) if значение else None
    return значение or None


def применить(
    conn,
    вид: Вид,
    номера: list[int],
    решение: str,
    проверяющий: str,
    правки: dict[str, str] | None = None,
    комментарий: str = "",
    секунды: int | None = None,
) -> int:
    """Записать решение человека: правка полей, статус, строка в журнале.

    Возвращает число затронутых объектов. Решение «черновик» означает
    сохранённую правку без вердикта — карточка остаётся в очереди.
    """
    правки = правки or {}
    if решение == "отклонён" and not комментарий.strip():
        raise ValueError("отклонение без причины не записывается")

    затронуто = 0
    for номер in номера:
        текущее = КАРТОЧКИ[вид.ключ](conn, номер)
        значения = {п: _приведение(вид, п, з) for п, з in правки.items() if п in вид.поля}
        изменения = _правки(текущее["значения_полей"], значения)

        присвоения = [f"{п} = %s" for п in значения]
        параметры = list(значения.values())
        присвоения.append("status = %s")
        параметры.append(решение)
        # Подпись человека, а не имя роли: триггер оставляет переданное значение.
        присвоения.append("confirmed_by = %s")
        параметры.append(проверяющий if решение == "подтверждён" else None)
        параметры.append(номер)

        conn.execute(
            f"UPDATE {вид.таблица} SET {', '.join(присвоения)} WHERE {вид.первичный} = %s",
            параметры,
        )
        conn.execute(
            """INSERT INTO meta.review
                   (reviewer, object_kind, object_id, object_ref, decision,
                    comment, edited, model, seconds)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (проверяющий, вид.название, номер, текущее.get("заголовок"), решение,
             комментарий or None,
             json.dumps(изменения, ensure_ascii=False, default=str) if изменения else None,
             текущее.get("model"), секунды),
        )
        затронуто += 1
    conn.commit()
    return затронуто


def паспорта_кода(conn, feature_id: int, статус: str = "черновик") -> list[int]:
    """Все паспорта этого статуса с тем же кодом признака.

    Один код признака приходит из нескольких сборок, и геологический смысл у них
    общий: решение человека распространяется на все, иначе очередь из 240
    паспортов — это 240 одинаковых прочтений вместо 85.

    Статус раньше был зашит в «черновик», и из-за этого по подтверждённому
    паспорту пакетное решение не делало ничего: искались черновики того же
    кода, их не было, список номеров выходил пустым. Галочки ставились,
    кнопка нажималась, в базе не менялось ничего.
    """
    строки = conn.execute(
        """SELECT p.feature_id FROM meta.feature_passport p
             JOIN data.feature f ON f.id = p.feature_id
            WHERE p.status = %s
              AND f.code = (SELECT code FROM data.feature WHERE id = %s)""",
        (статус, feature_id),
    ).fetchall()
    return [н for (н,) in строки]


# --------------------------------------------------------------------- журнал
def журнал(conn, сколько: int = 100) -> list[dict]:
    строки = conn.execute(
        """SELECT at, reviewer, object_kind, object_ref, decision, comment,
                  edited, model, seconds
             FROM meta.review ORDER BY at DESC LIMIT %s""",
        (сколько,),
    ).fetchall()
    имена = ("время", "проверяющий", "вид", "объект", "решение",
             "комментарий", "правка", "модель", "секунд")
    return [dict(zip(имена, с)) for с in строки]


def оценка_моделей(conn) -> list[dict]:
    строки = conn.execute(
        """SELECT модель, решений, подтверждено, отклонено, "с правкой",
                  "доля принятых" FROM meta.model_score ORDER BY решений DESC"""
    ).fetchall()
    имена = ("модель", "решений", "подтверждено", "отклонено",
             "с правкой", "доля принятых")
    return [dict(zip(имена, с)) for с in строки]
