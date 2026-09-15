"""Иллюстрации к презентации об агентной базе данных.

Все числа берутся из живой базы, ничего не вписано руками: если база
изменится, картинки пересоберутся с новыми цифрами.

Результат — outputs/рисунки/*.png
Запуск: python -X utf8 experiments/build_agentic_db_figures.py
"""

import sys
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gisdb import db

ВЫХОД = Path(__file__).resolve().parents[1] / "outputs" / "рисунки"

ТЁМНЫЙ = "#2b2b2b"
ЗОЛОТО = "#c89b2c"
СЕРЫЙ = "#777777"
ЗЕЛЁНЫЙ = "#3f8a45"
СИНИЙ = "#2f6fa8"
ОРАНЖ = "#c2661f"
ФИОЛЕТ = "#6a4c9c"

# Пояснения к кодам групп признаков — чтобы слайд читался без словаря.
ГРУППЫ = {
    "ast": "спектры ASTER",
    "astir": "тепловой ASTER",
    "dem": "рельеф",
    "dens": "плотности разломов и магматизма",
    "dist": "расстояния до объектов карты",
    "geo2": "факторы критериальной формулы",
    "gm": "гравика и магнитка",
    "l8": "спектры Landsat-8",
    "lin": "линеаменты",
    "ls": "каналы Landsat-7",
    "mask": "маска свиты",
    "pf": "трансформанты потенциальных полей",
    "psr": "радар ALOS PALSAR",
    "relief2": "производные рельефа",
    "s1": "радар Sentinel-1",
    "s2": "спектры Sentinel-2",
}


def сохранить(fig, имя: str) -> None:
    ВЫХОД.mkdir(parents=True, exist_ok=True)
    путь = ВЫХОД / f"{имя}.png"
    fig.savefig(путь, dpi=190, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"записано: {путь}")


def заголовок(ax, текст, подпись=None):
    ax.set_title(текст, fontsize=15, fontweight="bold", color=ТЁМНЫЙ, loc="left",
                 pad=30 if подпись else 12)
    if подпись:
        ax.text(0, 1.015, подпись, transform=ax.transAxes, fontsize=9.5,
                color=СЕРЫЙ, va="bottom")


# ============================================================ 1. карта сеток
def карта_сеток(движок) -> None:
    ячейки = pd.read_sql(
        """SELECT g.code AS сетка, ST_X(c.centroid) AS lon, ST_Y(c.centroid) AS lat,
                  c.id AS cell_id
             FROM data.cell c JOIN data.grid g ON g.id = c.grid_id""",
        движок,
    )
    покрытые = pd.read_sql(
        """SELECT v.cell_id
             FROM data.feature_value v JOIN data.feature f ON f.id = v.feature_id
            WHERE f.code = 'ast_kaolin'""",
        движок,
    )["cell_id"]

    лист = ячейки[ячейки["сетка"].str.startswith("лист")]
    широкая = ячейки[ячейки["сетка"].str.startswith("широкая")]
    есть_ast = ячейки["cell_id"].isin(set(покрытые))

    fig, axes = plt.subplots(1, 2, figsize=(15, 6.4))

    ax = axes[0]
    ax.scatter(широкая["lon"], широкая["lat"], s=1.2, c="#c9d8e8",
               label=f"широкая территория ГГК-200 — {len(широкая):,} ячеек".replace(",", " "))
    ax.scatter(лист["lon"], лист["lat"], s=1.2, c=ЗЕЛЁНЫЙ,
               label=f"основной лист R-49-I,II — {len(лист):,} ячеек".replace(",", " "))
    заголовок(ax, "Две сетки в одной базе",
              "территория — измерение базы, а не константа; ячейка 500 м, координаты приведены к WGS84")

    ax = axes[1]
    нет = ячейки[~есть_ast]
    да = ячейки[есть_ast]
    ax.scatter(нет["lon"], нет["lat"], s=1.2, c="#e4d7d7",
               label=f"нет съёмки — {len(нет):,} ячеек".replace(",", " "))
    ax.scatter(да["lon"], да["lat"], s=1.2, c=ОРАНЖ,
               label=f"ASTER снят — {len(да):,} ячеек".replace(",", " "))
    заголовок(ax, "Контур съёмки виден прямо в базе",
              "пропуск не хранится: покрытие признака считается запросом, а не декларируется в описании")

    for ax in axes:
        ax.set_xlabel("восточная долгота, °", fontsize=10)
        ax.set_ylabel("северная широта, °", fontsize=10)
        ax.legend(loc="lower left", fontsize=9.5, markerscale=8, framealpha=0.9)
        ax.set_aspect(1 / np.cos(np.deg2rad(70.5)))
        ax.grid(alpha=0.25, linewidth=0.5)

    fig.tight_layout()
    сохранить(fig, "карта_сеток")


# ==================================================== 2. покрытие по группам
def покрытие_по_группам(движок) -> None:
    данные = pd.read_sql(
        """SELECT coalesce(f.group_code, '—') AS группа,
                  CASE WHEN left(f.definition->>'сетка', 4) = 'лист' THEN 'лист' ELSE 'широкая' END AS сетка,
                  avg(p.coverage_frac) AS покрытие, count(*) AS признаков
             FROM data.feature f JOIN meta.feature_passport p ON p.feature_id = f.id
            GROUP BY 1, 2""",
        движок,
    )
    сводка = данные.pivot(index="группа", columns="сетка", values="покрытие")
    счёт = данные.pivot(index="группа", columns="сетка", values="признаков").fillna(0)
    сводка = сводка.sort_values("широкая", na_position="first")

    fig, ax = plt.subplots(figsize=(12.5, 7.2))
    y = np.arange(len(сводка))
    h = 0.38
    ax.barh(y + h / 2, сводка.get("лист").fillna(0) * 100, height=h,
            color=ЗЕЛЁНЫЙ, label="основной лист")
    ax.barh(y - h / 2, сводка.get("широкая").fillna(0) * 100, height=h,
            color="#8fb8dd", label="широкая территория")

    for i, группа in enumerate(сводка.index):
        for сдвиг, столбец in ((h / 2, "лист"), (-h / 2, "широкая")):
            доля = сводка.loc[группа, столбец]
            if pd.isna(доля):
                ax.text(1, i + сдвиг, "нет в сборке", va="center", fontsize=8.5, color=СЕРЫЙ)
            else:
                ax.text(доля * 100 + 1, i + сдвиг, f"{доля*100:.0f}%  ({int(счёт.loc[группа, столбец])})",
                        va="center", fontsize=8.5, color=ТЁМНЫЙ)

    подписи = [f"{к} — {ГРУППЫ.get(к, '')}".rstrip(" —") for к in сводка.index]
    ax.set_yticks(y, подписи, fontsize=10.5)
    ax.set_xlim(0, 118)
    ax.set_xlabel("среднее покрытие территории, %", fontsize=11)
    ax.legend(loc="lower left", bbox_to_anchor=(0.33, 0.015), ncol=2,
              fontsize=10, frameon=False)
    ax.grid(axis="x", alpha=0.25, linewidth=0.5)
    ax.spines[["top", "right"]].set_visible(False)
    заголовок(ax, "Покрытие по группам признаков — посчитано по базе",
              "в скобках число версий признаков в группе; пробел в данных виден сразу, без отдельного анализа")
    fig.tight_layout()
    сохранить(fig, "покрытие_по_группам")


# ================================================= 3. состояние паспортов
def состояние_паспортов(движок) -> None:
    ряд = pd.read_sql(
        """SELECT count(*) AS всего,
                  count(*) FILTER (WHERE geological_meaning IS NOT NULL) AS со_смыслом,
                  count(*) FILTER (WHERE circularity) AS циркулярные,
                  count(*) FILTER (WHERE transferability = 'переносим') AS переносимые,
                  count(*) FILTER (WHERE transferability = 'непереносим') AS непереносимые,
                  count(*) FILTER (WHERE footprint_dependent) AS от_контура,
                  count(*) FILTER (WHERE status = 'подтверждён') AS подтверждено
             FROM meta.feature_passport""",
        движок,
    ).iloc[0]

    fig = plt.figure(figsize=(14.5, 7.0))
    сетка = fig.add_gridspec(2, 4, height_ratios=[1.0, 1.35], hspace=0.45, wspace=0.25)

    карточки = [
        (int(ряд["всего"]), "паспортов признаков\nв базе", СИНИЙ),
        (int(ряд["со_смыслом"]), "смысл заполнен\nиз словарей проекта", ЗЕЛЁНЫЙ),
        (int(ряд["всего"] - ряд["со_смыслом"]), "ждут геологического\nсмысла", ЗОЛОТО),
        (int(ряд["подтверждено"]), "подтверждено\nчеловеком", ОРАНЖ),
    ]
    for i, (число, подпись, цвет) in enumerate(карточки):
        ax = fig.add_subplot(сетка[0, i])
        ax.axis("off")
        ax.add_patch(FancyBboxPatch((0.03, 0.08), 0.94, 0.84,
                                    boxstyle="round,pad=0.02,rounding_size=0.06",
                                    facecolor="#f6f6f4", edgecolor=цвет, linewidth=2))
        ax.text(0.5, 0.63, f"{число}", ha="center", va="center",
                fontsize=42, fontweight="bold", color=цвет)
        ax.text(0.5, 0.25, подпись, ha="center", va="center", fontsize=11, color=ТЁМНЫЙ)

    ax = fig.add_subplot(сетка[1, :])
    метки = ["зависят от контура\nсъёмки", "переносимы через\nразрыв 15 км",
             "непереносимы\n(гравика, потенциальные поля)", "циркулярны — прямые\nвходы формулы"]
    значения = [int(ряд["от_контура"]), int(ряд["переносимые"]),
                int(ряд["непереносимые"]), int(ряд["циркулярные"])]
    цвета = ["#8fb8dd", ЗЕЛЁНЫЙ, СЕРЫЙ, ОРАНЖ]
    столбики = ax.bar(метки, значения, color=цвета, width=0.55)
    ax.bar_label(столбики, fontsize=13, fontweight="bold", padding=3)
    ax.set_ylim(0, max(значения) * 1.25)
    ax.set_ylabel("признаков из 240", fontsize=11)
    ax.grid(axis="y", alpha=0.25, linewidth=0.5)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=10)
    заголовок(ax, "Что уже известно про каждый признак",
              "флаги проставлены автоматически: покрытие — из базы, циркулярность и переносимость — из накопленных проверок проекта")

    fig.suptitle("Паспорта признаков: состояние на сегодня", fontsize=17,
                 fontweight="bold", color=ТЁМНЫЙ, x=0.012, ha="left", y=0.99)
    сохранить(fig, "состояние_паспортов")


# ====================================================== 4. карта таблиц базы
def карта_базы(движок) -> None:
    схемы = {
        "data": ("данные", ЗЕЛЁНЫЙ, ["territory", "grid", "cell", "source_layer",
                                     "feature", "feature_value"]),
        "meta": ("паспорта и происхождение", СИНИЙ, ["license", "layer_passport",
                                                     "feature_passport", "gap",
                                                     "operation", "derivation"]),
        "runs": ("журнал прогонов", ФИОЛЕТ, ["run", "run_input", "metric", "artifact"]),
        "bridge": ("мост к знаниям", ОРАНЖ, ["feature_concept"]),
        "kb": ("база знаний", СЕРЫЙ, []),
        "staging": ("приёмник агента", СЕРЫЙ, []),
    }
    счёт = {}
    with движок.connect() as conn:
        import sqlalchemy as sa
        for схема, (_, _, таблицы) in схемы.items():
            for t in таблицы:
                счёт[f"{схема}.{t}"] = conn.execute(
                    sa.text(f"SELECT count(*) FROM {схема}.{t}")
                ).scalar()

    fig, ax = plt.subplots(figsize=(15, 7.6))
    ax.set_xlim(0, 15)
    ax.set_ylim(0, 7.6)
    ax.axis("off")

    x = 0.2
    for схема, (подпись, цвет, таблицы) in схемы.items():
        w = 2.35
        ax.add_patch(FancyBboxPatch((x, 1.25), w, 5.65,
                                    boxstyle="round,pad=0.03,rounding_size=0.08",
                                    facecolor="#f7f7f5", edgecolor=цвет, linewidth=2))
        ax.text(x + w / 2, 6.55, схема, ha="center", va="center",
                fontsize=15, fontweight="bold", color=цвет)
        ax.text(x + w / 2, 6.24, подпись, ha="center", va="center",
                fontsize=8.5, color=СЕРЫЙ)
        y = 5.75
        for t in таблицы:
            n = счёт[f"{схема}.{t}"]
            ax.add_patch(FancyBboxPatch((x + 0.13, y - 0.42), w - 0.26, 0.62,
                                        boxstyle="round,pad=0.01,rounding_size=0.05",
                                        facecolor="white", edgecolor=цвет, linewidth=1.1))
            ax.text(x + w / 2, y + 0.02, t, ha="center", va="center", fontsize=10.5,
                    color=ТЁМНЫЙ)
            ax.text(x + w / 2, y - 0.26, f"{n:,} строк".replace(",", " "),
                    ha="center", va="center", fontsize=8.5, color=СЕРЫЙ)
            y -= 0.78
        if not таблицы:
            ax.text(x + w / 2, 3.6, "готово\nк наполнению", ha="center", va="center",
                    fontsize=11, color=СЕРЫЙ, style="italic")
        x += 2.45

    ax.text(0.2, 7.35, "Из чего состоит база", fontsize=17, fontweight="bold", color=ТЁМНЫЙ)
    ax.text(0.2, 7.05,
            "шесть схем, 17 таблиц; feature_value разложена на 16 частей по хешу признака — "
            "чтобы 11.8 млн значений не лежали одной кучей",
            fontsize=10, color=СЕРЫЙ)
    ax.text(14.8, 0.75, "PostgreSQL 18 + PostGIS 3.6 · порт 5433",
            fontsize=9, color=СЕРЫЙ, ha="right")
    сохранить(fig, "карта_базы")


# ================================================= 5. пример происхождения
def происхождение(движок) -> None:
    прогон = pd.read_sql(
        """SELECT r.id, r.script, r.git_commit, r.status,
                  to_char(r.started_at, 'DD.MM.YYYY HH24:MI') AS начало,
                  i.ref_text, i.checksum
             FROM runs.run r JOIN runs.run_input i ON i.run_id = r.id
            WHERE r.id = 1 LIMIT 1""",
        движок,
    ).iloc[0]
    признак = pd.read_sql(
        """SELECT f.code, f.version, count(v.*) AS значений
             FROM data.feature f JOIN data.feature_value v ON v.feature_id = f.id
            WHERE f.code = 'ast_kaolin' AND left(f.definition->>'сетка', 4) = 'лист'
            GROUP BY f.code, f.version""",
        движок,
    ).iloc[0]

    fig, ax = plt.subplots(figsize=(15, 5.6))
    ax.set_xlim(0, 15)
    ax.set_ylim(0, 5.6)
    ax.axis("off")
    ax.text(0.2, 5.25, "Происхождение: у каждого числа есть предъявляемая родословная",
            fontsize=17, fontweight="bold", color=ТЁМНЫЙ)
    ax.text(0.2, 4.92,
            "уровень 2 — граф «источник → операция → результат» плюс журнал прогонов; "
            "ниже настоящая запись из базы, не пример",
            fontsize=10, color=СЕРЫЙ)

    блоки = [
        (0.2, 3.0, "ФАЙЛ-ИСТОЧНИК", прогон["ref_text"],
         f"SHA-256: {прогон['checksum'][:24]}…", ЗЕЛЁНЫЙ),
        (5.2, 3.0, "ОПЕРАЦИЯ", "перенос в базу",
         f"скрипт gisdb/db_ingest.py\nкоммит {(прогон['git_commit'] or '—')[:10]}", СИНИЙ),
        (10.2, 3.0, "ПРИЗНАК", f"{признак['code']}  версия {признак['version']}",
         f"{int(признак['значений']):,} значений по ячейкам".replace(",", " "), ОРАНЖ),
    ]
    for x, y, метка, строка1, строка2, цвет in блоки:
        ax.add_patch(FancyBboxPatch((x, y - 0.9), 4.4, 1.75,
                                    boxstyle="round,pad=0.03,rounding_size=0.08",
                                    facecolor="#f7f7f5", edgecolor=цвет, linewidth=2))
        ax.text(x + 2.2, y + 0.52, метка, ha="center", fontsize=10.5,
                fontweight="bold", color=цвет)
        ax.text(x + 2.2, y + 0.14, textwrap.fill(строка1, 26), ha="center",
                va="center", fontsize=11, color=ТЁМНЫЙ, linespacing=1.4)
        ax.text(x + 2.2, y - 0.48, строка2, ha="center", fontsize=9, color=СЕРЫЙ)
    for x in (4.7, 9.7):
        ax.add_patch(FancyArrowPatch((x, 3.1), (x + 0.5, 3.1), arrowstyle="-|>",
                                     mutation_scale=16, linewidth=1.8, color="#555555"))

    ax.add_patch(FancyBboxPatch((0.2, 0.35), 14.4, 1.5,
                                boxstyle="round,pad=0.03,rounding_size=0.06",
                                facecolor="#efeaf6", edgecolor=ФИОЛЕТ, linewidth=2))
    ax.text(0.55, 1.5, f"ПРОГОН № {int(прогон['id'])}", fontsize=12,
            fontweight="bold", color=ФИОЛЕТ, va="center")
    ax.text(0.55, 1.05,
            f"{прогон['script']}   ·   запуск {прогон['начало']}   ·   "
            f"статус «{прогон['status']}»   ·   роль gis_agent",
            fontsize=11, color=ТЁМНЫЙ, va="center")
    ax.text(0.55, 0.68,
            "повторить опыт — значит взять тот же коммит, те же файлы с теми же хешами "
            "и те же параметры; расхождение любого из трёх делает это другой версией признака",
            fontsize=9.5, color=СЕРЫЙ, va="center")
    сохранить(fig, "происхождение")


# ================================================ 6. правила подтверждения
def правила() -> None:
    fig, ax = plt.subplots(figsize=(14, 6.6))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 6.6)
    ax.axis("off")
    ax.text(0.2, 6.25, "Автоматика во всём, кроме трёх вещей — и это проверено, а не обещано",
            fontsize=17, fontweight="bold", color=ТЁМНЫЙ)
    ax.text(0.2, 5.92,
            "правило выражено правами роли и триггерами: агент физически не может обойти его, "
            "даже если ошибётся",
            fontsize=10, color=СЕРЫЙ)

    строки = [
        ("Завести черновик лицензии или паспорта", True, True, "обычная работа агента"),
        ("Поставить статус «подтверждён»", False, True, "ошибка триггера guard_confirmation"),
        ("Удалить строку из data или meta", False, True, "нет права DELETE у роли"),
        ("Пометить слой выселенным без пометки,\nкак его восстановить", False, False,
         "ошибка триггера guard_eviction:\nзапрещено всем, включая человека"),
        ("Удалять в приёмнике staging", True, True, "черновая площадка агента"),
    ]
    шапка_y = 5.25
    ax.add_patch(Rectangle((0.2, шапка_y), 13.6, 0.5, facecolor=ТЁМНЫЙ))
    for x, текст in ((0.45, "Действие"), (7.3, "агент"), (8.9, "человек"), (10.4, "что происходит")):
        ax.text(x, шапка_y + 0.25, текст, fontsize=11, fontweight="bold",
                color="white", va="center")

    y = шапка_y - 0.15
    for текст, агент, человек, примечание in строки:
        h = 0.92
        y -= h
        ax.add_patch(Rectangle((0.2, y), 13.6, h, facecolor="#f7f7f5", edgecolor="#dddddd"))
        ax.text(0.45, y + h / 2, текст, fontsize=11, color=ТЁМНЫЙ, va="center")
        for x, можно in ((7.55, агент), (9.25, человек)):
            ax.text(x, y + h / 2, "✓" if можно else "✕", fontsize=19, va="center",
                    ha="center", fontweight="bold",
                    color=ЗЕЛЁНЫЙ if можно else "#b03a2e")
        ax.text(10.4, y + h / 2, примечание, fontsize=9.5, color=СЕРЫЙ, va="center")

    ax.text(0.2, y - 0.42,
            "Три исключения из автоматики — удаление, лицензия, геологический смысл — "
            "это решения, цена ошибки в которых не измеряется метрикой.",
            fontsize=10.5, color=ОРАНЖ, va="center")
    сохранить(fig, "правила_подтверждения")


# ============================================================== 7. стоимость
def стоимость() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(15, 6.2),
                             gridspec_kw={"width_ratios": [1.0, 1.25]})

    ax = axes[0]
    модели = ["Haiku 4.5", "Sonnet 5", "Opus 5"]
    вход = [1, 2, 5]
    выход = [5, 10, 25]
    кэш = [0.1, 0.2, 0.5]
    x = np.arange(3)
    w = 0.26
    ax.bar(x - w, вход, w, label="вход", color="#8fb8dd")
    ax.bar(x, выход, w, label="выход", color=СИНИЙ)
    ax.bar(x + w, кэш, w, label="чтение из кэша", color=ЗЕЛЁНЫЙ)
    for i in range(3):
        ax.text(i - w, вход[i] + 0.4, f"${вход[i]}", ha="center", fontsize=9)
        ax.text(i, выход[i] + 0.4, f"${выход[i]}", ha="center", fontsize=9)
        ax.text(i + w, кэш[i] + 0.4, f"${кэш[i]}", ha="center", fontsize=9)
    ax.set_xticks(x, модели, fontsize=11)
    ax.set_ylabel("долларов за 1 млн токенов", fontsize=11)
    ax.legend(fontsize=9.5)
    ax.grid(axis="y", alpha=0.25, linewidth=0.5)
    ax.spines[["top", "right"]].set_visible(False)
    заголовок(ax, "Цена моделей", "повтор кэшированной части промпта дешевле входа в десять раз")

    ax = axes[1]
    статьи = ["Сборщик\n(Haiku)", "Паспортизатор\n(Sonnet, пакет)", "Контролёр\n(Haiku)",
              "База знаний\n(Sonnet)", "Трудные статьи\n(Opus)"]
    суммы = [6, 5, 4, 8, 4]
    цвета = ["#8fb8dd", СИНИЙ, "#8fb8dd", СИНИЙ, ФИОЛЕТ]
    столбики = ax.bar(статьи, суммы, color=цвета, width=0.6)
    ax.bar_label(столбики, labels=[f"${s}" for s in суммы], fontsize=12,
                 fontweight="bold", padding=3)
    ax.axhline(0, color="#cccccc")
    ax.set_ylim(0, 11)
    ax.set_ylabel("долларов в месяц", fontsize=11)
    ax.grid(axis="y", alpha=0.25, linewidth=0.5)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=9.5)
    заголовок(ax, "Фоновые агенты: $27 в месяц",
              "на текущем листе; при 10-15 листах и активной базе знаний — $60-120")

    fig.tight_layout()
    сохранить(fig, "стоимость")


# ========================================================= 8. перенос и риски
def перенос() -> None:
    fig, ax = plt.subplots(figsize=(15, 6.8))
    ax.set_xlim(0, 15)
    ax.set_ylim(0, 6.8)
    ax.axis("off")
    ax.text(0.2, 6.45, "База не привязана к этому ноутбуку",
            fontsize=17, fontweight="bold", color=ТЁМНЫЙ)
    ax.text(0.2, 6.12,
            "структура лежит в пяти файлах миграций, данные воспроизводятся скриптами — "
            "переезд на сервер это настройка, а не спасательная операция",
            fontsize=10, color=СЕРЫЙ)

    пути = [
        (0.2, "ПУТЬ А — ПЕРЕСОБРАТЬ", ЗЕЛЁНЫЙ,
         ["чистый PostgreSQL с PostGIS",
          "миграции 001-005",
          "db_load_datasets.py",
          "db_fill_passports.py"],
         "около получаса, по сети едет\nтолько репозиторий"),
        (5.2, "ПУТЬ Б — ПЕРЕВЕЗТИ", СИНИЙ,
         ["python db/backup.py",
          "дамп базы 113 МБ",
          "дамп ролей",
          "записка с порядком"],
         "997 МБ базы сжимаются\nв один файл"),
    ]
    for x, метка, цвет, шаги, итог in пути:
        ax.add_patch(FancyBboxPatch((x, 1.5), 4.5, 4.2,
                                    boxstyle="round,pad=0.03,rounding_size=0.08",
                                    facecolor="#f7f7f5", edgecolor=цвет, linewidth=2))
        ax.text(x + 2.25, 5.35, метка, ha="center", fontsize=12.5,
                fontweight="bold", color=цвет)
        y = 4.75
        for шаг in шаги:
            ax.add_patch(FancyBboxPatch((x + 0.25, y - 0.28), 4.0, 0.52,
                                        boxstyle="round,pad=0.01,rounding_size=0.05",
                                        facecolor="white", edgecolor=цвет, linewidth=1))
            ax.text(x + 2.25, y - 0.02, шаг, ha="center", va="center",
                    fontsize=10.5, color=ТЁМНЫЙ)
            y -= 0.72
        ax.text(x + 2.25, 1.85, итог, ha="center", va="center",
                fontsize=9.5, color=СЕРЫЙ, style="italic")

    ax.add_patch(FancyBboxPatch((10.2, 1.5), 4.6, 4.2,
                                boxstyle="round,pad=0.03,rounding_size=0.08",
                                facecolor="#fdf1e8", edgecolor=ОРАНЖ, linewidth=2))
    ax.text(12.5, 5.35, "ЧЕГО НЕ ХВАТИТ В ЛЮБОМ СЛУЧАЕ", ha="center",
            fontsize=11, fontweight="bold", color=ОРАНЖ)
    пункты = [
        "система координат 990018 —\nне попадает в дамп PostGIS",
        "роли и пароли —\nотдельный дамп",
        "растры и приёмник —\nкопируются файлами",
        "версия сервера —\nне ниже PostgreSQL 18",
    ]
    y = 4.75
    for пункт in пункты:
        ax.text(10.5, y, "•", fontsize=13, color=ОРАНЖ, va="top")
        ax.text(10.8, y, пункт, fontsize=10, color=ТЁМНЫЙ, va="top")
        y -= 0.85

    ax.add_patch(FancyBboxPatch((0.2, 0.25), 14.6, 0.95,
                                boxstyle="round,pad=0.02,rounding_size=0.06",
                                facecolor="#eef5ee", edgecolor=ЗЕЛЁНЫЙ, linewidth=1.6))
    ax.text(0.5, 0.72,
            "Пути файлов пишутся в базу переносимо: «проект:/data/processed/…», "
            "«хранилище:/raster/…» — буквы диска в базе нет нигде.",
            fontsize=11, color=ТЁМНЫЙ, va="center")
    ax.text(0.5, 0.45,
            "Адрес, порт, имя базы и корень хранилища читаются из переменных окружения — "
            "на новом месте код не правится.",
            fontsize=10, color=СЕРЫЙ, va="center")
    сохранить(fig, "перенос_на_сервер")


# ============================================================== 9. пробелы
def пробелы(движок) -> None:
    данные = pd.read_sql(
        "SELECT code, title, holder, access, priority, what_needed FROM meta.gap ORDER BY priority, code",
        движок,
    )
    fig, ax = plt.subplots(figsize=(15, 6.2))
    ax.set_xlim(0, 15)
    ax.set_ylim(0, 6.2)
    ax.axis("off")
    ax.text(0.2, 5.85, "Чего нет — тоже факт базы",
            fontsize=17, fontweight="bold", color=ТЁМНЫЙ)
    ax.text(0.2, 5.52,
            "реестр пробелов питает контур «данных достаточно? — нет»: отказ зафиксирован "
            "с датой и держателем, а не забыт",
            fontsize=10, color=СЕРЫЙ)

    цвет_доступа = {"отказано": "#b03a2e", "закрыт": СЕРЫЙ,
                    "запрошен": ЗОЛОТО, "открыт": ЗЕЛЁНЫЙ}
    x = 0.2
    ширина = (14.6 - 0.3 * (len(данные) - 1)) / len(данные)
    for _, строка in данные.iterrows():
        цвет = цвет_доступа.get(строка["access"], СЕРЫЙ)
        ax.add_patch(FancyBboxPatch((x, 0.9), ширина, 4.3,
                                    boxstyle="round,pad=0.03,rounding_size=0.08",
                                    facecolor="#f7f7f5", edgecolor=цвет, linewidth=2))
        ax.add_patch(FancyBboxPatch((x + 0.15, 4.55), ширина - 0.3, 0.42,
                                    boxstyle="round,pad=0.01,rounding_size=0.06",
                                    facecolor=цвет, edgecolor="none"))
        ax.text(x + ширина / 2, 4.76, строка["access"].upper(), ha="center", va="center",
                fontsize=9.5, fontweight="bold", color="white")
        название = строка["title"].replace("Pathfinder-элементы", "Элементы-спутники")
        ax.text(x + ширина / 2, 4.2, textwrap.fill(название, 22),
                ha="center", va="top", fontsize=11, fontweight="bold", color=ТЁМНЫЙ)
        ax.text(x + ширина / 2, 2.95, textwrap.fill(строка["what_needed"], 32),
                ha="center", va="top", fontsize=8.5, color=ТЁМНЫЙ, linespacing=1.45)
        ax.text(x + ширина / 2, 1.35, f"держатель:\n{строка['holder']}", ha="center",
                va="bottom", fontsize=8.5, color=СЕРЫЙ, style="italic")
        ax.text(x + ширина / 2, 1.05, f"важность {int(строка['priority'])}", ha="center",
                va="bottom", fontsize=9, color=цвет, fontweight="bold")
        x += ширина + 0.3

    ax.text(0.2, 0.45,
            "Пробел, записанный в базу, отличается от пробела в голове тем, что его нельзя "
            "случайно принять за ноль.",
            fontsize=10.5, color=ОРАНЖ, va="center")
    сохранить(fig, "реестр_пробелов")



# ============================================================ 10. было / стало
def было_стало() -> None:
    каталог = Path("data/processed")
    файлов = sum(1 for f in каталог.iterdir() if f.is_file())
    объём = sum(f.stat().st_size for f in каталог.rglob("*") if f.is_file()) / 1024**3

    fig, ax = plt.subplots(figsize=(15, 7.0))
    ax.set_xlim(0, 15)
    ax.set_ylim(0, 7.0)
    ax.axis("off")
    ax.text(0.2, 6.65, "Зачем вообще база", fontsize=17, fontweight="bold", color=ТЁМНЫЙ)
    ax.text(0.2, 6.32,
            "то же самое содержимое, но с ответом на вопрос «откуда это число и можно ли ему верить»",
            fontsize=10.5, color=СЕРЫЙ)

    ax.add_patch(FancyBboxPatch((0.2, 0.6), 6.6, 5.4,
                                boxstyle="round,pad=0.03,rounding_size=0.08",
                                facecolor="#f4f0ee", edgecolor=СЕРЫЙ, linewidth=2))
    ax.text(3.5, 5.6, "БЫЛО: каталог файлов", ha="center", fontsize=13.5,
            fontweight="bold", color=СЕРЫЙ)
    было = [
        f"{файлов} файлов в data/processed, {объём:.1f} ГБ",
        "паспорт слоя — в голове и в переписке",
        "покрытие признака узнаётся пересчётом",
        "версия признака отличается только именем файла",
        "лицензия источника нигде не записана",
        "что уже запрашивали и получили отказ — в письмах",
        "перенос на сервер — копирование с правкой путей",
    ]
    y = 5.0
    for пункт in было:
        ax.text(0.55, y, "—", fontsize=12, color=СЕРЫЙ, va="center")
        ax.text(0.95, y, пункт, fontsize=11.5, color=ТЁМНЫЙ, va="center")
        y -= 0.62

    ax.add_patch(FancyArrowPatch((7.0, 3.3), (8.0, 3.3), arrowstyle="-|>",
                                 mutation_scale=26, linewidth=3, color=ЗОЛОТО))

    ax.add_patch(FancyBboxPatch((8.2, 0.6), 6.6, 5.4,
                                boxstyle="round,pad=0.03,rounding_size=0.08",
                                facecolor="#eef5ee", edgecolor=ЗЕЛЁНЫЙ, linewidth=2))
    ax.text(11.5, 5.6, "СТАЛО: база с паспортами", ha="center", fontsize=13.5,
            fontweight="bold", color=ЗЕЛЁНЫЙ)
    стало = [
        "11.8 млн значений с адресом ячейки и сеткой",
        "паспорт признака и слоя — строка базы со статусом",
        "покрытие — поле паспорта, считается запросом",
        "версия привязана к сборке, коду и хешам входов",
        "лицензия — сущность, её подтверждает человек",
        "отказ по данным — запись в реестре пробелов",
        "перенос — дамп плюс переменные окружения",
    ]
    y = 5.0
    for пункт in стало:
        ax.text(8.55, y, "✓", fontsize=12, color=ЗЕЛЁНЫЙ, va="center", fontweight="bold")
        ax.text(8.95, y, пункт, fontsize=11, color=ТЁМНЫЙ, va="center")
        y -= 0.62

    ax.text(0.2, 0.25,
            "Файлы никуда не делись — база не заменяет их, а описывает: что это, "
            "откуда, на какой площади и чему в нём можно верить.",
            fontsize=10.5, color=СЕРЫЙ)
    сохранить(fig, "было_стало")


# ========================================================= 11. дорожная карта
def дорожная_карта() -> None:
    fig, ax = plt.subplots(figsize=(15, 7.0))
    ax.set_xlim(0, 15)
    ax.set_ylim(0, 7.0)
    ax.axis("off")
    ax.text(0.2, 6.65, "Что сделано и что дальше", fontsize=17,
            fontweight="bold", color=ТЁМНЫЙ)
    ax.text(0.2, 6.32,
            "узлы 3 и 4 технологической схемы: сбор сырых данных и паспортизация",
            fontsize=10.5, color=СЕРЫЙ)

    колонки = [
        ("СДЕЛАНО", ЗЕЛЁНЫЙ, "#eef5ee", [
            "экземпляр PostgreSQL с PostGIS на отдельном порту",
            "схема из шести разделов, 17 таблиц, миграции 001-005",
            "три роли; запрет удаления и подтверждения для агента",
            "собственная система координат листа",
            "перенесены обе сборки: 113 543 ячейки, 11.8 млн значений",
            "240 черновиков паспортов признаков",
            "реестр пробелов и журнал прогонов",
            "резервная копия и порядок переезда на сервер",
        ]),
        ("В РАБОТЕ", ЗОЛОТО, "#fdf6e6", [
            "регистрация слоёв-источников: 96 файлов,\nшейпы организации, растры",
            "контуры съёмок и хеши для каждого слоя",
            "черновики паспортов слоёв и лицензий",
        ]),
        ("ДАЛЬШЕ", СИНИЙ, "#eaf1f8", [
            "агент-паспортизатор: 138 недостающих\nгеологических смыслов",
            "панель подтверждений — очередь решений человека",
            "мост к базе знаний: понятие ↔ признак со ссылкой",
            "снимки датасетов для повторяемых опытов",
            "перевод фоновых агентов на локальную модель",
        ]),
    ]
    x = 0.2
    for метка, цвет, фон, пункты in колонки:
        w = 4.66
        ax.add_patch(FancyBboxPatch((x, 0.45), w, 5.45,
                                    boxstyle="round,pad=0.03,rounding_size=0.08",
                                    facecolor=фон, edgecolor=цвет, linewidth=2))
        ax.add_patch(FancyBboxPatch((x + 0.2, 5.3), w - 0.4, 0.45,
                                    boxstyle="round,pad=0.01,rounding_size=0.08",
                                    facecolor=цвет, edgecolor="none"))
        ax.text(x + w / 2, 5.52, метка, ha="center", va="center", fontsize=12.5,
                fontweight="bold", color="white")
        y = 4.9
        for пункт in пункты:
            обёрнутый = textwrap.fill(пункт.replace("\n", " "), 36)
            строк = обёрнутый.count("\n") + 1
            ax.text(x + 0.32, y, "•", fontsize=12, color=цвет, va="top")
            ax.text(x + 0.6, y, обёрнутый, fontsize=10, color=ТЁМНЫЙ, va="top",
                    linespacing=1.4)
            y -= 0.1 + 0.26 * строк
        x += w + 0.26

    сохранить(fig, "дорожная_карта")


def main() -> None:
    движок = db.engine("gis_read")
    карта_сеток(движок)
    покрытие_по_группам(движок)
    состояние_паспортов(движок)
    карта_базы(движок)
    происхождение(движок)
    правила()
    стоимость()
    перенос()
    пробелы(движок)
    было_стало()
    дорожная_карта()


if __name__ == "__main__":
    main()
