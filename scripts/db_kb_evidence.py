"""Мост к базе знаний статей: литература в обоснование паспортов признаков.

Каждый паспорт признака кончается сейчас словами «публикация не привязана,
база знаний в разработке». База знаний статей (репозиторий georag, отдельная
машина) уже умеет отвечать на вопрос «что в статьях написано про понятие X» —
фрагментами с дословной цитатой, DOI и страницей. Этот скрипт задаёт ей такой
вопрос по каждому понятию, к которому привязаны черновики паспортов, и
записывает ответ.

Запуск:
    python scripts/db_kb_evidence.py                — все черновики паспортов
    python scripts/db_kb_evidence.py --сухой        — показать, что пришло, ничего не писать
    python scripts/db_kb_evidence.py --на-понятие 5  — сколько статей брать на понятие (3)
    python scripts/db_kb_evidence.py --адрес http://localhost:8000

Где база знаний: переменная GIS_KB_URL, по умолчанию http://localhost:8000.
На машине базы знаний должен работать сервер: `python georag.py serve`.
Если она на другой машине — к ней ходят через SSH-туннель, и адрес остаётся
тем же localhost:8000: туннель подставляет чужой порт на свой.

Как связаны две базы. Их общий узел — понятие: код в kb.concept здесь и код в
словаре базы знаний совпадают буква в букву. Отсюда цепочка

    признак → (bridge.feature_concept) → понятие → (база знаний) → статья, цитата

Паспорта сюда не копируются никуда и ниоткуда: база знаний про признаки не
знает, она знает понятия.

Что записывается — и всё черновиком:

* kb.publication — статьи, kb.citation — цитаты под понятия (миграция 012);
* в конец обоснования (kb_reference) черновика паспорта — блок «Литература из
  базы знаний статей» со ссылками и цитатами. Человек видит его в той же
  карточке панели, где подтверждает смысл признака. Повторный запуск блок
  заменяет, а не дописывает второй;
* граф происхождения: публикация → паспорт (meta.derivation).

Подтверждённые паспорта не трогаются: их меняет только человек.

Как выбираются статьи. Сначала — по нашей территории и по методу, которым
посчитан признак (у ast — ASTER, у lin — линеаментный анализ). Нет таких —
ослабляем: только территория, только метод, просто понятие. В блоке видно,
какой отбор сработал, чтобы «по Билляхской зоне» не путалось с «вообще».
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gisdb import db  # noqa: E402
from gisdb import db_ingest as ingest  # noqa: E402

СКРИПТ = "scripts/db_kb_evidence.py"
АДРЕС = os.environ.get("GIS_KB_URL", "http://localhost:8000")

# Территории проекта — от узкой к широкой. Имена — как в словаре базы знаний.
ТЕРРИТОРИИ = ("Билляхская зона", "Анабарский щит")

# Метод, которым посчитана группа признаков, — имя метода в словаре базы знаний.
# Группы без однозначного метода (gm — и гравика, и магнитка) сюда не попадают.
МЕТОД_ПО_ГРУППЕ = {
    "ast": "ASTER", "astir": "ASTER",
    "l8": "Landsat", "ls": "Landsat",
    "s2": "Sentinel", "s1": "Sentinel",
    "lin": "линеаментный анализ",
}

МЕТКА = "Литература из базы знаний статей"
ЗАГЛУШКА = re.compile(r";?\s*публикация не привязана,\s*база знаний в разработке\.?")


# --------------------------------------------------------------- база знаний
class НетСвязи(RuntimeError):
    """База знаний не ответила — дальше идти бессмысленно."""


def спросить(адрес: str, путь: str, тело: dict | None = None, таймаут: int = 60) -> dict:
    """GET или POST к базе знаний; ответ — JSON. Ошибки с понятным текстом."""
    данные = json.dumps(тело, ensure_ascii=False).encode("utf-8") if тело is not None else None
    запрос = urllib.request.Request(
        адрес.rstrip("/") + путь, data=данные,
        method="POST" if тело is not None else "GET",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(запрос, timeout=таймаут) as ответ:
            return json.loads(ответ.read().decode("utf-8"))
    except urllib.error.HTTPError as ошибка:
        try:
            подробно = json.loads(ошибка.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            подробно = {"error": str(ошибка)}
        if ошибка.code == 503:
            raise НетСвязи(f"база знаний не готова: {подробно.get('error')}") from None
        return {"ошибка": ошибка.code, **подробно}
    except (urllib.error.URLError, OSError) as ошибка:
        raise НетСвязи(
            f"база знаний не отвечает по адресу {адрес}: {ошибка}. На её машине должна "
            "быть запущен сервер (python georag.py serve), а с другой машины — поднят "
            "SSH-туннель к порту 8000"
        ) from None


def варианты(территории: tuple[str, ...], метод: str | None) -> list[tuple[str | None, str | None]]:
    """Отбор от строгого к мягкому: территория и метод → территория → метод → ничего."""
    out: list[tuple[str | None, str | None]] = []
    for т in территории:
        if метод:
            out.append((т, метод))
        out.append((т, None))
    if метод:
        out.append((None, метод))
    out.append((None, None))
    return out


def статьи_по_понятию(адрес: str, понятие: str, метод: str | None, территории, сколько: int):
    """Первый отбор, который что-то нашёл: (территория, метод, фрагменты, as_of)."""
    for территория, м in варианты(территории, метод):
        запрос = {"concept": понятие, "limit": сколько, "per_doc": 1}
        if территория:
            запрос["territory"] = территория
        if м:
            запрос["method"] = м
        ответ = спросить(адрес, "/api/concept", запрос)
        if "ошибка" in ответ:
            raise ValueError(f"{понятие}: {ответ.get('error')} {ответ.get('detail', '')}".strip())
        if ответ.get("fragments"):
            return территория, м, ответ["fragments"], ответ.get("as_of")
    return None, None, [], None


# ------------------------------------------------------------ текст обоснования
def без_старого_блока(текст: str | None) -> str:
    """Обоснование без заглушки «публикация не привязана» и без прошлого блока литературы."""
    текст = текст or ""
    if МЕТКА in текст:
        текст = текст[: текст.index(МЕТКА)]
    return ЗАГЛУШКА.sub("", текст).rstrip(" ;\n")


def ссылка(ц: dict) -> str:
    авторы = ", ".join(ц.get("authors") or [])
    if авторы and len(ц.get("authors") or []) >= 3:
        авторы += " и др."
    год = f", {ц['year']}" if ц.get("year") else ""
    где = f"doi:{ц['doi']}" if ц.get("doi") else (ц.get("url") or "")
    стр = f", с. {ц['page']}" if ц.get("page") else ""
    цитата = ц["quote"] if len(ц["quote"]) <= 300 else ц["quote"][:297].rsplit(" ", 1)[0] + "…"
    проверка = "; фрагмент подтверждён моделью базы знаний" \
        if ц.get("verdict") == "подтверждено моделью" else ""
    return f"{авторы}{год}. {ц.get('title') or ''}. {где}{стр} — «{цитата}»{проверка}".strip()


def блок(по_понятиям: list[dict]) -> str:
    """Блок литературы для конца обоснования; по понятиям, с пометкой об отборе."""
    строки = [f"{МЕТКА} (черновик, проверяет человек):"]
    for п in по_понятиям:
        отбор = ", ".join(x for x in (п["территория"], п["метод"]) if x) or "без отбора по территории и методу"
        строки.append(f"[{п['понятие']} — {отбор}]")
        if not п["фрагменты"]:
            строки.append("  в базе знаний статей ничего не нашлось")
        for i, ц in enumerate(п["фрагменты"], 1):
            строки.append(f"  {i}. {ссылка(ц)}")
    return "\n".join(строки)


# ------------------------------------------------------------------ работа
РАБОТА = """
SELECT p.feature_id, f.code, f.group_code, b.concept_code, p.kb_reference
  FROM meta.feature_passport p
  JOIN data.feature f ON f.id = p.feature_id
  JOIN bridge.feature_concept b ON b.feature_id = p.feature_id
 WHERE p.status = 'черновик' AND b.status <> 'отклонён'
 ORDER BY f.code, p.feature_id, b.concept_code
"""


def записать_публикацию(conn, ц: dict) -> int:
    строка = conn.execute(
        """INSERT INTO kb.publication (kb_doc_id, doi, title, year, url, authors, journal)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (kb_doc_id) DO UPDATE SET
               doi = coalesce(EXCLUDED.doi, kb.publication.doi),
               title = EXCLUDED.title, year = EXCLUDED.year, url = EXCLUDED.url,
               authors = EXCLUDED.authors, journal = EXCLUDED.journal
           RETURNING id""",
        (ц["doc_id"], ц.get("doi") or None, ц.get("title"), ц.get("year"), ц.get("url"),
         ц.get("authors") or [], ц.get("journal")),
    ).fetchone()
    return строка[0]


def записать_цитату(conn, run_id: int, публикация: int, понятие: str, ц: dict,
                    территория, метод, as_of) -> None:
    """Новая цитата — черновиком; уже известная обновляется, только пока она черновик.

    Цитату, по которой человек вынес решение, агент не трогает: триггер
    не дал бы, а прогон упал бы на середине.
    """
    conn.execute(
        """INSERT INTO kb.citation (publication_id, concept_code, quote, page, territory,
                                    method, kb_similarity, kb_found_by, kb_verdict, kb_as_of,
                                    run_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT ON CONSTRAINT citation_once DO UPDATE SET
               kb_similarity = EXCLUDED.kb_similarity, kb_found_by = EXCLUDED.kb_found_by,
               kb_verdict = EXCLUDED.kb_verdict, kb_as_of = EXCLUDED.kb_as_of,
               run_id = EXCLUDED.run_id
           WHERE kb.citation.status = 'черновик'""",
        (публикация, понятие, ц["quote"], ц.get("page"), территория, метод,
         ц.get("similarity"), ц.get("found_by"), ц.get("verdict"), as_of, run_id),
    )


def main(argv: list[str]) -> int:
    сухой = "--сухой" in argv

    def аргумент(имя: str, умолчание=None):
        return argv[argv.index(имя) + 1] if имя in argv else умолчание

    адрес = аргумент("--адрес", АДРЕС)
    сколько = int(аргумент("--на-понятие", 3))

    try:
        словарь = спросить(адрес, "/api/concepts")
    except НетСвязи as ошибка:
        print(ошибка, file=sys.stderr)
        return 1
    понятия_бз = {c["code"] for c in словарь.get("concepts", [])}
    методы_бз = set(словарь.get("methods", []))
    территории = tuple(т for т in ТЕРРИТОРИИ if т in set(словарь.get("territories", [])))
    print(f"база знаний: {адрес}, разметка от {словарь.get('as_of')}, "
          f"статей {словарь.get('corpus', {}).get('documents')}")
    if словарь.get("warning"):
        print(f"  предупреждение базы знаний: {словарь['warning']}")

    with db.connect("gis_agent") as conn:
        наши = {r[0] for r in conn.execute("SELECT code FROM kb.concept").fetchall()}
        if наши - понятия_бз:
            print("  понятий нет в словаре базы знаний, по ним спросить нельзя: "
                  + "; ".join(sorted(наши - понятия_бз)))

        строки = conn.execute(РАБОТА).fetchall()
        if not строки:
            print("черновиков паспортов со связью «признак — понятие» нет: спрашивать не о чем")
            return 0

        # Один вопрос на пару (понятие, метод): у десятков признаков она общая.
        пары = sorted({(понятие, МЕТОД_ПО_ГРУППЕ.get(группа or "") if
                        МЕТОД_ПО_ГРУППЕ.get(группа or "") in методы_бз else None)
                       for _, _, группа, понятие, _ in строки if понятие in понятия_бз},
                      key=lambda p: (p[0], p[1] or ""))
        ответы: dict[tuple, dict] = {}
        for понятие, метод in пары:
            try:
                территория, м, фрагменты, as_of = статьи_по_понятию(
                    адрес, понятие, метод, территории, сколько)
            except (ValueError, НетСвязи) as ошибка:
                print(f"  {понятие}: {ошибка}", file=sys.stderr)
                continue
            ответы[(понятие, метод)] = {"понятие": понятие, "территория": территория,
                                        "метод": м, "фрагменты": фрагменты, "as_of": as_of}
            отбор = ", ".join(x for x in (территория, м) if x) or "без отбора"
            print(f"  {понятие}{' / ' + метод if метод else ''}: {len(фрагменты)} стат. ({отбор})")

        # Что пойдёт в каждый паспорт: по его понятиям, с его методом.
        паспорта: dict[int, dict] = {}
        for feature_id, код, группа, понятие, обоснование in строки:
            метод = МЕТОД_ПО_ГРУППЕ.get(группа or "")
            ответ = ответы.get((понятие, метод if метод in методы_бз else None))
            if ответ is None:
                continue
            п = паспорта.setdefault(feature_id, {"код": код, "обоснование": обоснование,
                                                 "понятия": []})
            п["понятия"].append(ответ)

        if сухой:
            for feature_id, п in list(паспорта.items())[:3]:
                print(f"\n--- {п['код']} (паспорт {feature_id}) ---\n{блок(п['понятия'])}")
            print(f"\nсухой прогон: паспортов к обновлению {len(паспорта)}, ничего не записано")
            return 0

        with ingest.run(conn, script=СКРИПТ,
                        note=f"литература из базы знаний: {len(паспорта)} паспортов") as run_id:
            публикации: dict[str, int] = {}
            цитат = 0
            for ответ in ответы.values():
                for ц in ответ["фрагменты"]:
                    if ц["doc_id"] not in публикации:
                        публикации[ц["doc_id"]] = записать_публикацию(conn, ц)
                    записать_цитату(conn, run_id, публикации[ц["doc_id"]], ответ["понятие"], ц,
                                    ответ["территория"], ответ["метод"], ответ["as_of"])
                    цитат += 1

            обновлено = 0
            for feature_id, п in паспорта.items():
                текст = без_старого_блока(п["обоснование"])
                новое = (текст + "\n\n" if текст else "") + блок(п["понятия"])
                сделано = conn.execute(
                    """UPDATE meta.feature_passport SET kb_reference = %s
                        WHERE feature_id = %s AND status = 'черновик'""",
                    (новое, feature_id),
                ).rowcount
                if not сделано:
                    continue          # пока шёл прогон, человек подтвердил — не трогаем
                обновлено += 1
                op_id = conn.execute(
                    """INSERT INTO meta.operation (kind, script, git_commit, params, run_id)
                       VALUES ('обоснование из литературы', %s, %s, %s, %s) RETURNING id""",
                    (СКРИПТ, ingest.git_commit(),
                     json.dumps({"база знаний": адрес}, ensure_ascii=False), run_id),
                ).fetchone()[0]
                for ответ in п["понятия"]:
                    for ц in ответ["фрагменты"]:
                        conn.execute(
                            """INSERT INTO meta.derivation
                                   (operation_id, source_kind, source_id, source_ref,
                                    target_kind, target_id)
                               VALUES (%s, 'публикация', %s, %s, 'паспорт', %s)""",
                            (op_id, публикации[ц["doc_id"]],
                             f"doi:{ц['doi']}" if ц.get("doi") else ц.get("url"), feature_id),
                        )
            ingest.add_metric(conn, run_id, "публикаций", len(публикации))
            ingest.add_metric(conn, run_id, "цитат", цитат)
            ingest.add_metric(conn, run_id, "паспортов обновлено", обновлено)
            conn.commit()
            print(f"\nзаписано: публикаций {len(публикации)}, цитат {цитат}, "
                  f"паспортов обновлено {обновлено}; прогон #{run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
