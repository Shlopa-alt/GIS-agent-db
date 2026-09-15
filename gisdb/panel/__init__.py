r"""Панель подтверждений: очередь черновиков, которую разбирает человек.

Агент заполняет паспорта, лицензии и связи со статусом «черновик» — статус
«подтверждён» база от него не примет (триггер `meta.guard_confirmation`).
Панель — рабочее место второй половины этого правила: человек читает черновик
вместе с фактами, на которых он построен, правит формулировку и выносит
решение. Каждое решение ложится в `meta.review`: кто, когда, что решил, почему
отклонил и что исправил.

Запуск:
    python -m gisdb.panel                 — http://127.0.0.1:8765
    python -m gisdb.panel --порт 9000

Про доступ честно: панель слушает только петлевой адрес и пароля не спрашивает.
Имя в поле «проверяющий» — подпись под решением, а не вход в систему. На сервере
с сетевым доступом перед ней должен стоять обычный вход по паролю, а роль
`gis_human` — получить пароль каждого проверяющего отдельно.

В базу панель ходит ролью `gis_human`: те же права, что у агента, плюс право
ставить «подтверждён». Суперпользователь для этого не нужен и опасен.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import quote, unquote

import jinja2
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from gisdb import db
from gisdb.panel import queries as q

ШАБЛОНЫ = jinja2.Environment(
    loader=jinja2.FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)

app = FastAPI(title="Панель подтверждений")


def показать(имя: str, **значения) -> HTMLResponse:
    return HTMLResponse(ШАБЛОНЫ.get_template(имя).render(**значения))


# Имя печенья — на латинице: протокол другого не принимает (http.cookies),
# а само имя человека внутри неё кодируется процентами.
ПЕЧЕНЬЕ = "reviewer"


def проверяющий(request: Request) -> str:
    return unquote(request.cookies.get(ПЕЧЕНЬЕ, ""))


def соединение():
    return db.connect("gis_human")


# ------------------------------------------------------------------- страницы
@app.get("/", response_class=HTMLResponse)
def главная(request: Request):
    кто = проверяющий(request)
    if not кто:
        return RedirectResponse("/вход", status_code=303)
    with соединение() as conn:
        return показать("index.html", кто=кто, сводка=q.сводка(conn),
                        оценка=q.оценка_моделей(conn), журнал=q.журнал(conn, 8))


@app.get("/вход", response_class=HTMLResponse)
def вход():
    return показать("login.html")


@app.post("/вход")
def войти(имя: str = Form(...)):
    ответ = RedirectResponse("/", status_code=303)
    # Подпись под решениями; хранится год, чтобы не вводить её каждый день.
    ответ.set_cookie(ПЕЧЕНЬЕ, quote(имя.strip()), max_age=365 * 24 * 3600)
    return ответ


# Имена подстановок в адресах — латиницей: разбор шаблона пути в Starlette
# понимает только ASCII-идентификаторы. Сами адреса остаются русскими.
@app.get("/очередь/{kind}", response_class=HTMLResponse)
def очередь(kind: str, request: Request, статус: str = "черновик"):
    вид = q.ВИДЫ[kind]
    with соединение() as conn:
        return показать("queue.html", кто=проверяющий(request), вид=вид,
                        статус=статус, статусы=q.СТАТУСЫ,
                        записи=q.очередь(conn, вид, статус))


@app.get("/карточка/{kind}/{num}", response_class=HTMLResponse)
def карточка(kind: str, num: int, request: Request):
    вид, номер = q.ВИДЫ[kind], num
    with соединение() as conn:
        данные = q.карточка(conn, вид, номер)
        осталось = len(q.очередь(conn, вид, "черновик"))
    return показать(
        f"card_{kind}.html", кто=проверяющий(request), вид=вид, к=данные,
        подписи=q.ПОДПИСИ, осталось=осталось, открыто=int(time.time()),
    )


@app.post("/решение/{kind}/{num}")
async def решение(kind: str, num: int, request: Request):
    вид, номер = q.ВИДЫ[kind], num
    форма = await request.form()
    кто = проверяющий(request) or "не подписан"
    выбор = форма.get("решение", "черновик")
    комментарий = (форма.get("комментарий") or "").strip()
    открыто = int(форма.get("открыто") or 0)
    секунды = int(time.time()) - открыто if открыто else None

    правки = {п: форма.get(п, "") for п in вид.поля if п in форма or п == "allows_redistribution"}

    with соединение() as conn:
        номера = [номер]
        if kind == "признаки" and форма.get("все_сборки") == "да":
            номера = q.паспорта_кода(conn, номер) or [номер]
        try:
            q.применить(conn, вид, номера, выбор, кто, правки, комментарий, секунды)
        except ValueError as ошибка:
            return показать("error.html", кто=кто, сообщение=str(ошибка),
                            назад=f"/карточка/{kind}/{номер}")
        дальше = q.следующий(conn, вид, номер)

    if выбор == "черновик":       # правка сохранена, решение не вынесено
        return RedirectResponse(f"/карточка/{kind}/{номер}", status_code=303)
    if дальше:
        return RedirectResponse(f"/карточка/{kind}/{дальше}", status_code=303)
    return RedirectResponse(f"/очередь/{kind}", status_code=303)


@app.post("/пакет/{kind}")
async def пакет(kind: str, request: Request):
    """Решение по нескольким черновикам сразу — из списка очереди."""
    вид = q.ВИДЫ[kind]
    форма = await request.form()
    кто = проверяющий(request) or "не подписан"
    выбор = форма.get("решение", "подтверждён")
    комментарий = (форма.get("комментарий") or "").strip()
    отмечено = [int(з) for з in форма.getlist("отмечено")]

    with соединение() as conn:
        номера: list[int] = []
        for номер in отмечено:
            номера += q.паспорта_кода(conn, номер) if kind == "признаки" else [номер]
        if not номера:
            return RedirectResponse(f"/очередь/{kind}", status_code=303)
        try:
            q.применить(conn, вид, номера, выбор, кто, {}, комментарий)
        except ValueError as ошибка:
            return показать("error.html", кто=кто, сообщение=str(ошибка),
                            назад=f"/очередь/{kind}")
    return RedirectResponse(f"/очередь/{kind}", status_code=303)


@app.get("/журнал", response_class=HTMLResponse)
def журнал(request: Request, сколько: int = 200):
    with соединение() as conn:
        return показать("journal.html", кто=проверяющий(request),
                        записи=q.журнал(conn, сколько),
                        оценка=q.оценка_моделей(conn))


def main(argv: list[str]) -> None:
    import uvicorn

    порт = int(argv[argv.index("--порт") + 1]) if "--порт" in argv else 8765
    uvicorn.run(app, host="127.0.0.1", port=порт, log_level="warning")


if __name__ == "__main__":
    main(sys.argv[1:])
