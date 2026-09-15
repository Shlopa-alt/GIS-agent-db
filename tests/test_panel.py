"""Панель подтверждений: очередь, карточка, запись решения.

Панель — рабочее место человека, и её поломка не видна из кода агентов: список
открывается, а вид объектов в нём пропадает. Проверки идут на живой базе (порт
5433) и откатываются; если база не поднята, они пропускаются.
"""
import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from fastapi.testclient import TestClient

from gisdb import db, panel
from gisdb.panel import queries as q


def соединение(роль: str):
    try:
        return db.connect(роль, connect_timeout=5)
    except Exception as беда:
        pytest.skip(f"база недоступна ({роль}): {беда}")


@pytest.fixture
def человек():
    conn = соединение("gis_human")
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def клиент():
    соединение("gis_human").close()                # база нужна и странице
    return TestClient(panel.app, cookies={"reviewer": "%D0%9F%D1%80%D0%BE%D0%B2%D0%B5%D1%80%D0%BA%D0%B0"})


@pytest.mark.parametrize("ключ", list(q.ВИДЫ))
def test_у_каждого_вида_есть_запрос_карточка_и_шаблон(ключ):
    assert ключ in q.СПИСКИ and ключ in q.КАРТОЧКИ
    шаблон = panel.ШАБЛОНЫ.get_template(f"card_{ключ}.html")
    assert шаблон is not None


@pytest.mark.parametrize("ключ", list(q.ВИДЫ))
def test_поля_вида_имеют_подписи(ключ):
    без_подписи = [п for п in q.ВИДЫ[ключ].поля if п not in q.ПОДПИСИ]
    assert not без_подписи, f"поля без человеческой подписи: {без_подписи}"


@pytest.mark.parametrize("ключ", list(q.ВИДЫ))
def test_очередь_и_карточка_открываются(клиент, ключ):
    список = клиент.get(f"/очередь/{ключ}")
    assert список.status_code == 200
    with соединение("gis_human") as conn:
        записи = q.очередь(conn, q.ВИДЫ[ключ], "черновик")
    if not записи:
        pytest.skip(f"в очереди «{ключ}» нет черновиков")
    карточка = клиент.get(f"/карточка/{ключ}/{записи[0]['id']}")
    assert карточка.status_code == 200
    assert f"/решение/{ключ}/{записи[0]['id']}" in карточка.text


def test_понятия_видны_в_общей_очереди(человек):
    понятий, в_очереди = человек.execute(
        """SELECT (SELECT count(*) FROM kb.concept),
                  (SELECT count(*) FROM meta.confirmation_queue WHERE вид = 'понятие')"""
    ).fetchone()
    assert понятий and понятий == в_очереди


def test_решение_по_понятию_пишется_в_журнал(человек, monkeypatch):
    вид = q.ВИДЫ["понятия"]
    записи = q.очередь(человек, вид, "черновик")
    if not записи:
        pytest.skip("в очереди понятий нет черновиков")
    номер = записи[0]["id"]
    # Решение проверяется внутри транзакции, которую откатит фикстура: строку
    # журнала стереть нельзя (у роли нет такого права — и это правильно), а
    # настоящее решение человека здесь подделывать нечем.
    monkeypatch.setattr(человек, "commit", lambda: None)

    q.применить(человек, вид, [номер], "подтверждён", "проверка", {}, "", 5)

    статус, кем = человек.execute(
        "SELECT status, confirmed_by FROM kb.concept WHERE id = %s", (номер,)
    ).fetchone()
    решение = человек.execute(
        """SELECT decision, reviewer FROM meta.review
            WHERE object_kind = 'понятие' AND object_id = %s
            ORDER BY at DESC LIMIT 1""", (номер,)
    ).fetchone()
    assert статус == "подтверждён" and кем == "проверка"
    assert решение == ("подтверждён", "проверка")
