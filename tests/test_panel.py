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

from urllib.parse import unquote

from gisdb import db, panel
from gisdb.panel import queries as q
from gisdb.panel import сеансы


from conftest import соединение  # noqa: E402  — одна попытка на весь прогон


@pytest.fixture
def человек():
    conn = соединение("gis_human")
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def клиент():
    """Клиент с открытым сеансом: вход идёт тем же путём, что у человека.

    Печенье вручную не подделать — в нём только случайный ключ, — поэтому
    сеанс открывается настоящим паролем роли `gis_human` из хранилища.
    """
    соединение("gis_human").close()                # база нужна и странице
    клиент = TestClient(panel.app)
    ответ = клиент.post("/вход", data={"имя": "gis_human",
                                       "пароль": db.password("gis_human")},
                        follow_redirects=False)
    assert ответ.status_code == 303, "вход не удался: панель не открыть"
    return клиент


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


def test_кнопка_всей_очереди_берёт_все_черновики(клиент, monkeypatch):
    """«Подтвердить всё» не зависит от галочек и помечается в журнале.

    Само решение здесь не записывается: применение подменено, проверяется
    только то, какие номера и какая причина до него доходят.
    """
    вид = q.ВИДЫ["слои"]
    with соединение("gis_human") as conn:
        черновики = {з["id"] for з in q.очередь(conn, вид, "черновик")}
    if len(черновики) < 2:
        pytest.skip("в очереди слоёв меньше двух черновиков")

    дошло = {}
    monkeypatch.setattr(
        panel.q, "применить",
        lambda conn, вид, номера, решение, кто, правки=None, комментарий="", секунды=None:
            дошло.update(номера=set(номера), решение=решение, комментарий=комментарий)
            or len(номера),
    )

    один = sorted(черновики)[0]
    клиент.post("/пакет/слои", data={"решение": "подтверждён", "отмечено": [str(один)]})
    assert дошло["номера"] == {один}                  # без флага — только отмеченное
    assert дошло["комментарий"] == ""

    клиент.post("/пакет/слои?вся_очередь=да",
                data={"решение": "подтверждён", "отмечено": [str(один)]})
    assert дошло["номера"] == черновики                # с флагом — вся очередь
    assert дошло["комментарий"] == panel.ПАКЕТОМ       # пометка в журнал проверок


# ----------------------------------------------------------------------- вход
def test_без_сеанса_страницы_ведут_на_вход():
    """Открытый порт без пароля больше не отдаёт ни очередь, ни журнал."""
    соединение("gis_read").close()
    гость = TestClient(panel.app)
    for адрес in ("/", "/очередь/признаки", "/журнал"):
        ответ = гость.get(адрес, follow_redirects=False)
        assert ответ.status_code == 303, адрес
        assert unquote(ответ.headers["location"]).startswith("/вход"), адрес


def test_решение_без_сеанса_не_проходит():
    """Главное: подписать решение, не войдя, нельзя — ни одним запросом."""
    соединение("gis_read").close()
    гость = TestClient(panel.app)
    ответ = гость.post("/решение/признаки/1",
                       data={"решение": "подтверждён"}, follow_redirects=False)
    assert ответ.status_code == 303
    assert unquote(ответ.headers["location"]).startswith("/вход")


def test_неверный_пароль_сеанса_не_открывает():
    соединение("gis_read").close()
    гость = TestClient(panel.app)
    ответ = гость.post("/вход", data={"имя": "gis_human", "пароль": "не тот"})
    assert ответ.status_code == 200
    assert "не подходят" in ответ.text
    assert not гость.cookies.get(сеансы.ПЕЧЕНЬЕ), "сеанс не должен открыться"


def test_подпись_берётся_из_учётной_записи_а_не_из_формы(клиент):
    """Подписаться чужим именем нельзя: имя берётся из сеанса, не из запроса."""
    страница = клиент.get("/")
    assert страница.status_code == 200
    assert "gis_human" in страница.text
    assert 'name="кто"' not in страница.text, "имени подписи нет среди полей формы"


def test_читатель_не_получает_кнопок_решения():
    """Право вердикта проверяет база; кнопку, которая получит отказ, не даём."""
    соединение("gis_read").close()
    читатель = TestClient(panel.app)
    ответ = читатель.post("/вход", data={"имя": "gis_read",
                                         "пароль": db.password("gis_read")},
                          follow_redirects=False)
    if ответ.status_code != 303:
        pytest.skip("роль gis_read не входит в панель на этой машине")
    страница = читатель.get("/очередь/признаки")
    assert страница.status_code == 200
    assert "только чтение" in страница.text
    assert "Подтвердить отмеченные" not in страница.text


def test_выход_гасит_сеанс(клиент):
    клиент.post("/выход", follow_redirects=False)
    ответ = клиент.get("/", follow_redirects=False)
    assert ответ.status_code == 303
    assert unquote(ответ.headers["location"]).startswith("/вход")


def test_истёкший_сеанс_просит_войти_заново(клиент, monkeypatch):
    monkeypatch.setattr(сеансы, "СРОК", -1)          # всё, что есть, уже истекло
    ответ = клиент.get("/", follow_redirects=False)
    assert ответ.status_code == 303
    assert "истёк" in unquote(ответ.headers["location"])
