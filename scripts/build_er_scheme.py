r"""Схема сущностей и связей базы данных — рисуется по живой базе.

Состав таблиц, столбцов, ключей и число строк читаются из самой базы
(`information_schema`, `pg_constraint`), поэтому схема не расходится с
действительностью: переименовали столбец — он переименован и на картинке.
Вручную задаётся только раскладка (кто где стоит) и русские пояснения.

Связи разводятся по правилам: соседние блоки соединяются напрямую, блоки
одной колонки — боковой шиной, далёкие — коридором поверху. Линия нигде не
проходит сквозь блок.

Запуск:  python scripts/build_er_scheme.py
Выход:   outputs/рисунки/схема_базы_ER.png (+ .pdf)
"""

import sys
from pathlib import Path

import matplotlib
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

from gisdb import db

ВЫХОД = Path(__file__).resolve().parents[1] / "outputs" / "рисунки"

# Цвета схем базы. Набор проверен на различимость при нарушениях
# цветовосприятия (проверка палитры, режим «светлый», все пары): зелёный,
# оранжевый, пурпурный, синий. Цветом обозначена только принадлежность к
# схеме — смысл везде продублирован подписью, поэтому цвет нигде не
# единственный признак.
ЦВЕТ = {
    "data":   ("#356b2f", "#e3efdf"),
    "meta":   ("#c07a1c", "#fbeada"),
    "runs":   ("#9c3f7a", "#f3e1ee"),
    "знания": ("#2f6fa8", "#dde9f5"),
}
ЧЕРНИЛА = "#1f1f1f"
ЧЕРНИЛА2 = "#555555"

ПОЯСНЕНИЕ = {
    "data.territory": "территория: лист карты и его граница",
    "data.grid": "регулярная сетка над территорией",
    "data.cell": "ячейка сетки: квадрат и его центр",
    "data.feature": "признак: код, версия, определение",
    "data.feature_value": "значение признака в ячейке",
    "data.source_layer": "исходный слой: откуда и чем покрыт",
    "meta.feature_passport": "паспорт признака: смысл и пригодность",
    "meta.layer_passport": "паспорт слоя: источник и условия",
    "meta.license": "условия использования слоя",
    "meta.gap": "пробел: данных нет, у кого просить",
    "meta.review": "решение человека по объекту",
    "meta.operation": "операция обработки данных",
    "meta.derivation": "что из чего получено",
    "meta.confirmation_queue": "очередь черновиков к человеку",
    "meta.model_score": "качество ответов модели",
    "kb.concept": "понятие предметной области",
    "bridge.feature_concept": "признак ↔ понятие: роль и основание",
    "runs.run": "прогон скрипта: когда, чем, с чем",
    "runs.llm_call": "обращение к модели и её ответ",
    "runs.metric": "показатель, измеренный в прогоне",
    "runs.artifact": "файл, полученный в прогоне",
    "runs.run_input": "что подавалось на вход прогону",
}

ГРУППА = {"data": "data", "meta": "meta", "runs": "runs",
          "kb": "знания", "bridge": "знания"}

# Раскладка: (левый край, ширина, таблицы сверху вниз). Порядок подобран так,
# чтобы ссылающаяся таблица стояла рядом с той, на которую ссылается.
КОЛОНКИ: list[tuple[float, float, list[str]]] = [
    (1.0, 17.0, ["data.territory", "data.grid", "data.cell",
                 "data.feature_value"]),
    (20.5, 18.5, ["data.source_layer", "data.feature"]),
    (42.0, 18.5, ["meta.layer_passport", "meta.license", "meta.gap",
                  "meta.feature_passport"]),
    (63.5, 18.0, ["kb.concept", "bridge.feature_concept", "meta.derivation",
                  "meta.review", "meta.confirmation_queue",
                  "meta.model_score"]),
    (85.0, 14.0, ["runs.run", "meta.operation", "runs.llm_call",
                  "runs.metric", "runs.artifact", "runs.run_input"]),
]
ВЕРХ = 51.5          # верхний край блоков
СТРОКА = 0.55        # высота строки атрибута
ШАПКА = 1.9          # высота заголовка блока
ПОДВАЛ = 0.35        # просвет под последним атрибутом
ЗАЗОР = 1.5          # просвет между блоками в колонке
КОРИДОР = (52.5, 53.6)   # уровни обхода поверху для далёких связей

ПРЕДСТАВЛЕНИЯ = {"meta.confirmation_queue", "meta.model_score"}

# Связи без ограничения внешнего ключа: столбец указывает на запись, но какой
# именно таблицы — решает соседний столбец `*_kind` или соглашение. База такую
# ссылку не проверяет, поэтому на схеме она пунктиром.
МЯГКИЕ = [
    ("data.feature", "runs.run", "produced_by_run"),
    ("meta.operation", "runs.run", "run_id"),
    ("meta.review", "meta.confirmation_queue", "из очереди"),
    ("meta.review", "meta.model_score", "по решениям"),
]

# Далёкие связи ведутся поверху: уровень коридора и сторона выхода.
ПОВЕРХУ = {
    ("bridge.feature_concept", "data.feature"): (КОРИДОР[0], "лево"),
    ("data.feature", "runs.run"): (КОРИДОР[1], "право"),
}

СОКРАЩЕНИЕ = {
    "character varying": "текст", "text": "текст", "integer": "целое",
    "bigint": "целое", "smallint": "целое", "double precision": "число",
    "numeric": "число", "boolean": "да/нет", "jsonb": "json",
    "timestamp with time zone": "время", "date": "дата",
    "USER-DEFINED": "набор",
}


# ------------------------------------------------------------ чтение базы
def состав() -> dict:
    """Таблицы, столбцы, ключи и число строк — прямо из базы."""
    показываем = {и for _, _, с in КОЛОНКИ for и in с}
    with db.engine("gis_read").connect() as c:
        столбцы = c.execute(sa.text("""
            SELECT table_schema, table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema IN ('data','meta','runs','kb','bridge')
            ORDER BY table_schema, table_name, ordinal_position""")).all()
        ключи = c.execute(sa.text("""
            SELECT t.relnamespace::regnamespace::text||'.'||t.relname,
                   con.contype, pg_get_constraintdef(con.oid),
                   CASE WHEN con.contype='f'
                        THEN f.relnamespace::regnamespace::text||'.'||f.relname
                        END
            FROM pg_constraint con
            JOIN pg_class t ON t.oid = con.conrelid
            LEFT JOIN pg_class f ON f.oid = con.confrelid
            WHERE con.contype IN ('p','f')
              AND t.relnamespace::regnamespace::text
                  IN ('data','meta','runs','kb','bridge')""")).all()
        секций = c.execute(sa.text("""
            SELECT count(*) FROM pg_inherits i
            JOIN pg_class p ON p.oid = i.inhparent
            WHERE p.relname = 'feature_value'""")).scalar()
        строк = {}
        for имя in sorted(показываем - ПРЕДСТАВЛЕНИЯ):
            строк[имя] = c.execute(
                sa.text(f"SELECT count(*) FROM {имя}")).scalar()

    поля: dict[str, list[tuple[str, str]]] = {}
    for сх, таб, поле, тип in столбцы:
        имя = f"{сх}.{таб}"
        if имя in показываем:
            поля.setdefault(имя, []).append((поле, СОКРАЩЕНИЕ.get(тип, тип)))

    пк: dict[str, set[str]] = {}
    фк: list[tuple[str, str, str]] = []
    for имя, вид, текст, цель in ключи:
        поля_ключа = [x.strip().strip('"') for x in
                      текст[текст.index("(") + 1:текст.index(")")].split(",")]
        if вид == "p" and имя in показываем:
            пк[имя] = set(поля_ключа)
        elif вид == "f" and имя in показываем and цель in показываем:
            фк.append((имя, цель, поля_ключа[0]))

    пропущены = sorted(показываем - set(поля))
    if пропущены:
        print("! на схеме есть, а в базе нет:", ", ".join(пропущены))
    return {"поля": поля, "пк": пк, "фк": sorted(фк), "строк": строк,
            "секций": секций}


# ------------------------------------------------------------- раскладка
def разложить(данные: dict) -> tuple[dict, dict]:
    """Координаты блоков (x, y_низ, ширина, высота) и их место в колонке."""
    места, адрес = {}, {}
    for к, (x, ширина, список) in enumerate(КОЛОНКИ):
        y = ВЕРХ
        for i, имя in enumerate(список):
            h = ШАПКА + СТРОКА * len(данные["поля"][имя]) + ПОДВАЛ
            места[имя] = (x, y - h, ширина, h)
            адрес[имя] = (к, i)
            y -= h + ЗАЗОР
    return места, адрес


def строк_словом(n: int) -> str:
    """«1 строка», «2 строки», «40 строк» — иначе подпись читается как ошибка."""
    число = f"{n:,}".replace(",", " ")
    хвост = n % 100
    if 11 <= хвост <= 14:
        слово = "строк"
    else:
        слово = {1: "строка", 2: "строки", 3: "строки", 4: "строки"}.get(
            n % 10, "строк")
    return f"{число} {слово}"


def центр(место):
    return место[0] + место[2] / 2, место[1] + место[3] / 2


def точка_на_краю(место, к_точке):
    """Где линия выходит из прямоугольника в сторону заданной точки."""
    cx, cy = центр(место)
    dx, dy = к_точке[0] - cx, к_точке[1] - cy
    if dx == 0 and dy == 0:
        return cx, cy
    t = min((место[2] / 2) / abs(dx) if dx else 1e9,
            (место[3] / 2) / abs(dy) if dy else 1e9)
    return cx + dx * t, cy + dy * t


# ------------------------------------------------------------- рисование
def нарисовать_блок(ax, имя, место, данные):
    x, y, w, h = место
    рамка, заливка = ЦВЕТ[ГРУППА[имя.split(".")[0]]]
    представление = имя in ПРЕДСТАВЛЕНИЯ

    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.35",
        linewidth=1.7, edgecolor=рамка, facecolor="white",
        linestyle=(0, (4, 3)) if представление else "solid", zorder=4))
    ax.add_patch(FancyBboxPatch(
        (x, y + h - ШАПКА), w, ШАПКА,
        boxstyle="round,pad=0,rounding_size=0.35",
        linewidth=0, facecolor=заливка, zorder=5))
    ax.plot([x, x + w], [y + h - ШАПКА] * 2, color=рамка, lw=0.9, zorder=6)

    подпись = имя + ("   (представление)" if представление else "")
    ax.text(x + 0.45, y + h - 0.72, подпись, fontsize=8.4, weight="bold",
            color=ЧЕРНИЛА, va="center", zorder=7)
    строк = данные["строк"].get(имя)
    хвост = "" if строк is None else f"   ·   {строк_словом(строк)}"
    ax.text(x + 0.45, y + h - 1.48, ПОЯСНЕНИЕ[имя] + хвост, fontsize=6.7,
            color=ЧЕРНИЛА2, va="center", zorder=7)

    пк = данные["пк"].get(имя, set())
    фк = {поле for и, _, поле in данные["фк"] if и == имя}
    мягкие = {поле for a, _, поле in МЯГКИЕ if a == имя}
    for i, (поле, тип) in enumerate(данные["поля"][имя]):
        yy = y + h - ШАПКА - 0.42 - i * СТРОКА
        метка = ("◆" if поле in пк else "▸" if поле in фк
                 else "⋯" if поле in мягкие else " ")
        ax.text(x + 0.45, yy, метка, fontsize=6.0, color=рамка, va="center",
                zorder=7)
        ax.text(x + 1.15, yy, поле, fontsize=6.3, color=ЧЕРНИЛА, va="center",
                family="DejaVu Sans Mono",
                weight="bold" if поле in пк else "normal", zorder=7)
        ax.text(x + w - 0.45, yy, тип, fontsize=5.8, color="#8f8f8f",
                va="center", ha="right", zorder=7)


def провести(ax, путь, цвет, подпись, пунктир):
    """Линия по заданным точкам со стрелкой на конце и подписью столбца."""
    точки, отрезок, шина_ли = путь
    стиль = (0, (4, 2.5)) if пунктир else "solid"
    xs = [т[0] for т in точки]
    ys = [т[1] for т in точки]
    ax.plot(xs[:-1], ys[:-1], color=цвет, lw=1.2, linestyle=стиль,
            solid_capstyle="round", zorder=2)
    ax.add_patch(FancyArrowPatch(
        точки[-2], точки[-1], arrowstyle="-|>", mutation_scale=10,
        linewidth=1.2, color=цвет, linestyle=стиль, zorder=2,
        shrinkA=0, shrinkB=1.0))

    i = отрезок
    вертикальный = abs(ys[i + 1] - ys[i]) > abs(xs[i + 1] - xs[i])
    доля = 0.5 if шина_ли else 0.62   # ближе к цели: у начала стоит метка «N»
    сx = xs[i] + (xs[i + 1] - xs[i]) * доля
    сy = ys[i] + (ys[i + 1] - ys[i]) * доля
    if вертикальный and шина_ли:
        # вдоль шины в зазоре между колонками подпись ставится боком
        ax.text(сx, сy, подпись, fontsize=5.8, color=цвет, ha="center",
                va="center", rotation=90, zorder=8,
                bbox=dict(boxstyle="round,pad=0.16", facecolor="white",
                          edgecolor="none", alpha=0.92))
    else:
        ax.text(сx + (1.15 if вертикальный else 0.0),
                сy + (0.0 if вертикальный else 0.32), подпись, fontsize=5.8,
                color=цвет, ha="left" if вертикальный else "center",
                va="center", zorder=8,
                bbox=dict(boxstyle="round,pad=0.16", facecolor="white",
                          edgecolor="none", alpha=0.92))
    for точка, соседняя, знак in ((точки[0], точки[1], "N"),
                                  (точки[-1], точки[-2], "1")):
        dx, dy = соседняя[0] - точка[0], соседняя[1] - точка[1]
        длина = (dx ** 2 + dy ** 2) ** 0.5 or 1.0
        # отступ вдоль линии, метка — сбоку от неё, чтобы не легла на саму линию
        ax.text(точка[0] + dx / длина * 0.95 - dy / длина * 0.38,
                точка[1] + dy / длина * 0.95 + dx / длина * 0.38,
                знак, fontsize=6.0, color=ЧЕРНИЛА2, ha="center", va="center",
                zorder=8)


def маршрут(места, адрес, откуда, куда, шина: dict) -> list:
    """Точки линии: напрямую, боковой шиной или коридором поверху."""
    a, b = места[откуда], места[куда]
    if (откуда, куда) in ПОВЕРХУ:
        уровень, сторона = ПОВЕРХУ[(откуда, куда)]
        ax_, ay = центр(a)
        bx, by = центр(b)
        край_a = (a[0] if сторона == "лево" else a[0] + a[2], ay)
        выход = (край_a[0] - 1.4 if сторона == "лево" else край_a[0] + 1.4, ay)
        спуск = b[0] - 1.6 if bx < ax_ else b[0] + b[2] + 1.6
        вход = (b[0] if bx < ax_ else b[0] + b[2], by)
        return [край_a, выход, (выход[0], уровень), (спуск, уровень),
                (спуск, by), вход], 2, False

    ка, кб = адрес[откуда], адрес[куда]
    if ка[0] == кб[0] and abs(ка[1] - кб[1]) > 1:      # одна колонка, не рядом
        сдвиг = 0.9 + 0.55 * шина.get(ка[0], 0)
        шина[ка[0]] = шина.get(ка[0], 0) + 1
        x = a[0] - сдвиг
        ay, by = центр(a)[1], центр(b)[1]
        return [(a[0], ay), (x, ay), (x, by), (b[0], by)], 1, True

    return [точка_на_краю(a, центр(b)),
            точка_на_краю(b, центр(a))], 0, False


def легенда(ax, блоков, связей):
    x0, y0 = 1.0, 59.6
    ax.text(x0, y0, "Схема сущностей и связей базы данных «золото-уран»",
            fontsize=15.5, weight="bold", color=ЧЕРНИЛА, va="center")
    ax.text(x0, y0 - 1.3,
            f"PostgreSQL 18 + PostGIS, база gis_au_u.  Таблиц и "
            f"представлений — {блоков}, связей, проверяемых базой — {связей}. "
            " Состав столбцов, ключи и число строк прочитаны из самой базы.",
            fontsize=8.2, color=ЧЕРНИЛА2, va="center")

    x = x0
    имена = {"data": "data — данные",
             "meta": "meta — паспорта и решения",
             "runs": "runs — прогоны и обращения к модели",
             "знания": "kb + bridge — понятия и связи с ними"}
    for схема, (рамка, заливка) in ЦВЕТ.items():
        ax.add_patch(FancyBboxPatch(
            (x, y0 - 3.15), 1.4, 0.7,
            boxstyle="round,pad=0,rounding_size=0.18",
            linewidth=1.5, edgecolor=рамка, facecolor=заливка))
        ax.text(x + 1.9, y0 - 2.8, имена[схема], fontsize=8, color=ЧЕРНИЛА,
                va="center")
        x += 2.1 + len(имена[схема]) * 0.45

    ax.text(x0, y0 - 4.55,
            "◆ ключ записи      ▸ ссылка, проверяемая базой      "
            "⋯ ссылка без проверки (на схеме пунктиром)      "
            "N → 1 — «много к одному»      пунктирная рамка — представление, "
            "своих строк не хранит",
            fontsize=7.6, color=ЧЕРНИЛА2, va="center")


def примечание(ax, данные, места):
    низ = места["data.feature_value"][1]
    текст = (
        f"Значение признака лежит в {данные['секций']} секциях "
        "data.feature_value_p0 … p15 — разрез по остатку\n"
        "от деления номера признака. У всех секций тот же состав столбцов и "
        "те же две ссылки,\nпоэтому на схеме показана одна родительская "
        "таблица.\n\n"
        "Ссылки meta.review.object_id, runs.run_input.ref_id и "
        "meta.derivation.source_id / target_id\nуказывают на запись любой "
        "таблицы: что это за запись, говорит соседний столбец *_kind.\n"
        "Такую ссылку база не проверяет — отсюда пунктир на схеме и отдельная "
        "пометка в списке\nстолбцов."
    )
    ax.text(1.0, низ - 2.2, текст, fontsize=7.6, color=ЧЕРНИЛА2, va="top",
            linespacing=1.6)


def main() -> None:
    данные = состав()
    места, адрес = разложить(данные)

    fig, ax = plt.subplots(figsize=(24, 14.5))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 61)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    легенда(ax, len(места), len(данные["фк"]))
    for имя, место in места.items():
        нарисовать_блок(ax, имя, место, данные)

    шина: dict = {}
    for источник, цель, поле in данные["фк"] + МЯГКИЕ:
        цвет = ЦВЕТ[ГРУППА[источник.split(".")[0]]][0]
        провести(ax, маршрут(места, адрес, источник, цель, шина), цвет, поле,
                 пунктир=(источник, цель, поле) in МЯГКИЕ)

    примечание(ax, данные, места)
    ВЫХОД.mkdir(parents=True, exist_ok=True)
    for расширение in ("png", "pdf"):
        путь = ВЫХОД / f"схема_базы_ER.{расширение}"
        fig.savefig(путь, dpi=200, bbox_inches="tight", facecolor="white")
        print("записано:", путь.name)
    plt.close(fig)


if __name__ == "__main__":
    main()
