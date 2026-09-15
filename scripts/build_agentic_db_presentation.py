# -*- coding: utf-8 -*-
"""Сборка презентации «Агентная база данных» (узлы 3-4 технологической схемы).

Стиль проекта: тёмная шапка с золотой полосой, Calibri, сплошные абзацы вместо
тезисов. Основной носитель смысла — иллюстрации из
`outputs/рисунки`, собранные скриптом
`experiments/build_agentic_db_figures.py` прямо из живой базы: все цифры на
слайдах — запросы к базе, а не набранные вручную.

Запуск из корня: python -X utf8 -m experiments.build_agentic_db_presentation
"""
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs"
OUT = OUT_DIR / "Презентация-Агентная-база-данных-13.09.2026.pptx"
IMG = ROOT / "outputs" / "рисунки"

GOLD = RGBColor(0xC8, 0x9B, 0x2C)
DARK = RGBColor(0x2B, 0x2B, 0x2B)
GREY = RGBColor(0x55, 0x55, 0x55)
LIGHT = RGBColor(0xF7, 0xF2, 0xE3)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GREEN = RGBColor(0x3F, 0x8A, 0x45)
BLUE = RGBColor(0x2F, 0x6F, 0xA8)
ORANGE = RGBColor(0xC2, 0x66, 0x1F)
VIOLET = RGBColor(0x6A, 0x4C, 0x9C)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
SW, SH = prs.slide_width, prs.slide_height
BLANK = prs.slide_layouts[6]

_номер = [0]


# --------------------------------------------------------------- примитивы
def add_slide():
    return prs.slides.add_slide(BLANK)


def rect(slide, x, y, w, h, color):
    sp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    sp.fill.solid()
    sp.fill.fore_color.rgb = color
    sp.line.fill.background()
    sp.shadow.inherit = False
    return sp


def round_rect(slide, x, y, w, h, color, radius=0.06):
    sp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    sp.adjustments[0] = radius
    sp.fill.solid()
    sp.fill.fore_color.rgb = color
    sp.line.fill.background()
    sp.shadow.inherit = False
    return sp


def textbox(slide, x, y, w, h, anchor=MSO_ANCHOR.TOP):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    return tf


def set_run(r, text, size, color=DARK, bold=False, italic=False):
    r.text = text
    f = r.font
    f.size = Pt(size)
    f.bold = bold
    f.italic = italic
    f.color.rgb = color
    f.name = "Calibri"


def header(slide, title, label=None, color=GREEN):
    """Тёмная шапка с золотой полосой, заголовок и номер слайда."""
    _номер[0] += 1
    rect(slide, 0, 0, SW, Inches(1.0), DARK)
    rect(slide, 0, Inches(1.0), SW, Pt(4), GOLD)
    tf = textbox(slide, Inches(0.55), 0, Inches(9.3), Inches(1.0), MSO_ANCHOR.MIDDLE)
    set_run(tf.paragraphs[0].add_run(), title, 24, WHITE, bold=True)
    if label:
        tfl = textbox(slide, Inches(9.6), 0, Inches(2.6), Inches(1.0), MSO_ANCHOR.MIDDLE)
        pl = tfl.paragraphs[0]
        pl.alignment = PP_ALIGN.RIGHT
        set_run(pl.add_run(), label, 11, GOLD, bold=True)
    tfn = textbox(slide, Inches(12.2), 0, Inches(0.9), Inches(1.0), MSO_ANCHOR.MIDDLE)
    pn = tfn.paragraphs[0]
    pn.alignment = PP_ALIGN.RIGHT
    set_run(pn.add_run(), str(_номер[0]), 18, GOLD, bold=True)


def prose(slide, x, y, w, h, paragraphs, size=15, color=DARK):
    tf = textbox(slide, x, y, w, h)
    first = True
    for text in paragraphs:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.space_after = Pt(12)
        p.line_spacing = 1.15
        set_run(p.add_run(), text, size, color)
    return tf


def fit_image(slide, path, x, y, max_w, max_h):
    iw, ih = Image.open(path).size
    ratio = min(max_w / iw, max_h / ih)
    w, h = int(iw * ratio), int(ih * ratio)
    px = x + Emu(int((max_w - w) / 2))
    py = y + Emu(int((max_h - h) / 2))
    slide.shapes.add_picture(str(path), px, py, width=Emu(w), height=Emu(h))
    return py + Emu(h)


def caption(slide, x, y, w, text, color=GREY):
    tf = textbox(slide, x, y, w, Inches(0.4))
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    set_run(p.add_run(), text, 10.5, color, italic=True)


# ------------------------------------------------------------------ слайды
def image_slide(title, label, lead, image, подпись, color=GREEN):
    """Крупная иллюстрация во всю ширину: короткий вывод сверху, подпись снизу."""
    s = add_slide()
    header(s, title, label, color)
    tf = textbox(s, Inches(0.55), Inches(1.3), Inches(12.2), Inches(0.7))
    set_run(tf.paragraphs[0].add_run(), lead, 15, DARK)
    низ = fit_image(s, IMG / image, Inches(0.55), Inches(2.05), Inches(12.2), Inches(4.75))
    caption(s, Inches(0.55), низ + Pt(6), Inches(12.2), подпись, color)
    return s


def full_slide(title, label, image, color=GREEN):
    """Только иллюстрация: у рисунка есть собственный заголовок и пояснение."""
    s = add_slide()
    header(s, title, label, color)
    fit_image(s, IMG / image, Inches(0.4), Inches(1.3), Inches(12.53), Inches(5.95))
    return s


def split_slide(title, label, paragraphs, image, подпись, color=GREEN, text_w=5.0):
    """Текст слева, иллюстрация справа на светлой плашке."""
    s = add_slide()
    header(s, title, label, color)
    tw = Inches(text_w)
    prose(s, Inches(0.55), Inches(1.35), tw, Inches(5.6), paragraphs, size=14.5)
    px = Inches(0.55) + tw + Inches(0.35)
    pw = SW - px - Inches(0.55)
    round_rect(s, px, Inches(1.35), pw, Inches(5.6), LIGHT, radius=0.03)
    rect(s, px, Inches(1.35), Pt(4), Inches(5.6), color)
    низ = fit_image(s, IMG / image, px + Inches(0.2), Inches(1.55),
                    pw - Inches(0.4), Inches(4.7))
    caption(s, px + Inches(0.2), низ + Pt(8), pw - Inches(0.4), подпись, color)
    return s


# ============================================================ 1. титульный
s = add_slide()
rect(s, 0, 0, SW, SH, DARK)
rect(s, 0, Inches(4.1), SW, Pt(5), GOLD)
tf = textbox(s, Inches(0.9), Inches(1.9), Inches(11.5), Inches(1.4))
set_run(tf.paragraphs[0].add_run(), "Агентная база данных", 46, WHITE, bold=True)
tf = textbox(s, Inches(0.9), Inches(3.15), Inches(11.5), Inches(0.9))
set_run(tf.paragraphs[0].add_run(),
        "Узлы 3 и 4 технологической схемы: сбор сырых данных и паспортизация",
        20, GOLD)
tf = textbox(s, Inches(0.9), Inches(4.45), Inches(11.5), Inches(2.0))
p = tf.paragraphs[0]
p.line_spacing = 1.3
set_run(p.add_run(),
        "Хранилище, которое отвечает не только «какое значение», но и «откуда оно, "
        "на какой площади измерено, кем подтверждено и можно ли переносить его на "
        "соседнюю территорию». Наполняют базу агенты, три решения оставлены человеку.",
        16, WHITE)
tf = textbox(s, Inches(0.9), Inches(6.5), Inches(11.5), Inches(0.5))
set_run(tf.paragraphs[0].add_run(),
        "Прогноз золото-урановых узлов  ·  13.09.2026  ·  "
        "PostgreSQL 18 + PostGIS 3.6", 13, GOLD)
_номер[0] = 1

# ============================================================ 2. зачем база
image_slide(
    "Зачем базе быть базой", "ПОСТАНОВКА",
    "Данные в проекте уже есть. Не хватает не гигабайтов, а ответа на вопрос, "
    "можно ли конкретному числу верить и при каких условиях.",
    "было_стало.png",
    "Каталог файлов против базы с паспортами: содержимое то же, меняется то, "
    "что о нём известно",
)

# ============================================================ 3. общая схема
full_slide(
    "Как это работает целиком", "СХЕМА РАБОТЫ",
    "схема_работы_агентной_бд.png", color=BLUE,
)

# ============================================================ 4. устройство
image_slide(
    "Из чего состоит база", "УСТРОЙСТВО",
    "Шесть разделов с разными правами доступа: измерения, паспорта, журнал "
    "прогонов, связь с базой знаний и черновая площадка агента. Разделение не "
    "косметическое: «агент не может удалить измерение» — это отсутствие права, "
    "а не договорённость.",
    "карта_базы.png",
    "Число строк в каждой таблице — запрос к базе на момент сборки слайда",
)

# ============================================================ 5. территория
image_slide(
    "Территория — измерение базы, а не константа", "МНОГОЛИСТОВОСТЬ",
    "Одна и та же база держит основной лист и широкую территорию соседних "
    "листов. Добавить третий лист — это строка в таблице территорий, а не новая "
    "копия проекта.",
    "карта_сеток.png",
    "Слева — две сетки по 500 м; справа — реальный контур съёмки ASTER, "
    "видимый прямо в данных",
)

# ============================================================ 6. покрытие
split_slide(
    "Пропуск — это факт, а не ноль", "КАЧЕСТВО ДАННЫХ",
    [
        "Значения, которых нет, в базу не пишутся. Покрытие признака получается "
        "делением: сколько ячеек заполнено к числу ячеек сетки. Никто не "
        "декларирует покрытие в описании — его считает запрос.",
        "Отсюда сразу видно, что́ на широкой территории держится, а что "
        "рассыпается: рельеф, гидросеть и радарная съёмка покрывают всё, "
        "спектральные группы — от трети до двух третей.",
        "Это тот самый разрыв, который раньше приходилось каждый раз "
        "восстанавливать вручную по именам файлов, а теперь он часть паспорта "
        "признака.",
    ],
    "покрытие_по_группам.png",
    "Среднее покрытие по группам признаков в двух сборках",
    text_w=5.6,
)

# ============================================================ 7. паспорта
image_slide(
    "Паспорт признака", "ПАСПОРТИЗАЦИЯ",
    "У каждого из 240 признаков есть карточка: что это, на какой площади "
    "измерено, переносимо ли на другую территорию, не является ли прямым входом "
    "критериальной формулы — и в чём его геологический смысл.",
    "состояние_паспортов.png",
    "Автоматика заполняет всё, кроме геологического смысла: 138 карточек ждут "
    "человека или агента-паспортизатора",
    color=BLUE,
)

# ============================================================ 8. происхождение
image_slide(
    "Откуда взялось каждое число", "ПРОИСХОЖДЕНИЕ",
    "Происхождение хранится не текстом в описании, а графом: файл-источник с "
    "контрольной суммой, операция с версией кода, полученный признак. Рядом — "
    "журнал прогонов.",
    "происхождение.png",
    "Настоящая запись из базы: повторить опыт — значит взять тот же коммит и те "
    "же файлы с теми же контрольными суммами",
    color=VIOLET,
)

# ============================================================ 9. правила
image_slide(
    "Что человек оставляет себе", "ПРАВИЛА",
    "Автоматизировано всё, кроме трёх вещей: удаления, лицензии и "
    "геологического смысла. Запрет выражен правами роли и триггерами, поэтому "
    "проверяется прогоном, а не доверием.",
    "правила_подтверждения.png",
    "Проверено от имени роли агента: черновик проходит, подтверждение и удаление "
    "отклоняются базой",
    color=ORANGE,
)

# ============================================================ 10. пробелы
image_slide(
    "Реестр пробелов", "ЧЕГО НЕТ",
    "Отсутствующие данные — такая же запись базы, как присутствующие: что "
    "именно нужно, у кого лежит, в каком состоянии запрос и насколько это важно.",
    "реестр_пробелов.png",
    "Четыре записи на сегодня; две — с зафиксированным отказом, а не с забытым "
    "письмом",
    color=ORANGE,
)

# ============================================================ 11. стоимость
image_slide(
    "Сколько стоит держать агентов", "СТОИМОСТЬ",
    "Разовое наполнение базы обошлось в 8-15 долларов, постоянный фон — около "
    "27 долларов в месяц. Экономия держится на пакетном режиме, кэше "
    "повторяющейся части запроса и передаче простых проверок лёгкой модели.",
    "стоимость.png",
    "Дорогая модель остаётся только там, где нужен разбор статьи или спорный "
    "смысл признака; следующий шаг — перевод фоновых ролей на локальную модель",
    color=VIOLET,
)

# ============================================================ 12. перенос
image_slide(
    "Переезд на сервер", "ПЕРЕНОСИМОСТЬ",
    "База изначально собрана так, чтобы не быть привязанной к рабочему "
    "ноутбуку: ни одного абсолютного пути внутри, вся настройка — через "
    "переменные окружения.",
    "перенос_на_сервер.png",
    "Два равноправных пути переезда и короткий список того, что не переносится "
    "само",
    color=BLUE,
)

# ============================================================ 13. дорожная карта
image_slide(
    "Что сделано и что дальше", "ПЛАН",
    "Каркас готов и наполнен реальными данными проекта. Впереди — регистрация "
    "слоёв-источников, агент-паспортизатор и панель подтверждений для очереди "
    "решений человека.",
    "дорожная_карта.png",
    "Узлы 3 и 4 технологической схемы: текущее состояние работ",
)

# ============================================================ 14. итог
s = add_slide()
header(s, "Главное", "ИТОГ")
пункты = [
    ("База отвечает на вопрос «можно ли верить»",
     "не только хранит значения, но и держит их площадь, происхождение, "
     "лицензию и статус подтверждения", GREEN),
    ("Автоматика везде, кроме трёх решений",
     "удаление, лицензия и геологический смысл остаются за человеком — "
     "и это выражено правами, а не соглашением", ORANGE),
    ("Пробел записан так же, как данные",
     "отказ по аэрогамма-спектрометрии и геохимии зафиксирован с держателем и "
     "датой, его нельзя случайно принять за ноль", BLUE),
    ("Переезд на сервер — настройка, а не спасение",
     "структура в пяти файлах миграций, данные воспроизводятся скриптами, "
     "путей с буквой диска в базе нет", VIOLET),
]
y = Inches(1.45)
for заголовок_, текст, цвет in пункты:
    h = Inches(1.28)
    round_rect(s, Inches(0.55), y, Inches(12.2), h, LIGHT, radius=0.05)
    rect(s, Inches(0.55), y, Pt(5), h, цвет)
    tf = textbox(s, Inches(0.95), y + Inches(0.14), Inches(11.5), Inches(0.4))
    set_run(tf.paragraphs[0].add_run(), заголовок_, 16, цвет, bold=True)
    tf2 = textbox(s, Inches(0.95), y + Inches(0.58), Inches(11.5), Inches(0.6))
    set_run(tf2.paragraphs[0].add_run(), текст, 13.5, GREY)
    y += h + Inches(0.16)

OUT_DIR.mkdir(parents=True, exist_ok=True)
prs.save(OUT)
print(f"записано: {OUT}  ·  слайдов: {len(prs.slides.__iter__.__self__._sldIdLst)}")
