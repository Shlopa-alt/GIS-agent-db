"""Правила, зашитые в базу: агент не удаляет, не подтверждает, не молчит.

Эти правила — единственное, что отличает агентную базу от свалки, куда модель
пишет что хочет. Пока они держатся на триггерах и правах, но не проверяются,
они остаются намерением; здесь они становятся проверяемым свойством.

Проверки идут на живой базе (экземпляр на F:, порт 5433) и ничего в ней не
меняют: каждая работает в своей транзакции, которая откатывается. Если база не
поднята, проверки пропускаются — на чужой машине репозиторий должен собираться
без неё.
"""
import pytest

psycopg = pytest.importorskip("psycopg")

from gisdb import db


def соединение(роль: str):
    try:
        return db.connect(роль, connect_timeout=5)
    except Exception as беда:                      # база не поднята — не наш случай
        pytest.skip(f"база недоступна ({роль}): {беда}")


@pytest.fixture
def агент():
    conn = соединение("gis_agent")
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def человек():
    conn = соединение("gis_human")
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def читатель():
    conn = соединение("gis_read")
    yield conn
    conn.rollback()
    conn.close()


def первый(conn, запрос: str) -> int:
    строка = conn.execute(запрос).fetchone()
    if not строка:
        pytest.skip("в базе нет подходящей записи для проверки")
    return строка[0]


# --------------------------------------------------------------- удаление
def test_агент_не_удаляет_вне_staging(агент):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        агент.execute("DELETE FROM meta.license WHERE id = -1")


def test_агент_полноправен_в_staging(агент):
    агент.execute("CREATE TABLE staging.проба_правил (id int)")
    агент.execute("INSERT INTO staging.проба_правил VALUES (1)")
    агент.execute("DELETE FROM staging.проба_правил")
    assert агент.execute("SELECT count(*) FROM staging.проба_правил").fetchone()[0] == 0


def test_читатель_не_пишет(читатель):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        читатель.execute(
            "INSERT INTO meta.gap (code, title) VALUES ('проба', 'проба правил')"
        )


# ----------------------------------------------------------- подтверждение
@pytest.mark.parametrize("таблица, ключ", [
    ("meta.license", "id"),
    ("meta.layer_passport", "layer_id"),
    ("meta.feature_passport", "feature_id"),
    ("kb.concept", "id"),
])
def test_агент_не_подтверждает(агент, таблица, ключ):
    номер = первый(агент, f"SELECT {ключ} FROM {таблица} LIMIT 1")
    with pytest.raises(psycopg.errors.RaiseException):
        агент.execute(
            f"UPDATE {таблица} SET status = 'подтверждён' WHERE {ключ} = %s", (номер,)
        )


def test_человек_подтверждает_и_подписывается(человек):
    номер = первый(человек, "SELECT id FROM meta.license LIMIT 1")
    человек.execute("UPDATE meta.license SET status = 'подтверждён' WHERE id = %s", (номер,))
    статус, кем, когда = человек.execute(
        "SELECT status, confirmed_by, confirmed_at FROM meta.license WHERE id = %s", (номер,)
    ).fetchone()
    assert статус == "подтверждён"
    assert кем == "gis_human" and когда is not None


def test_снятие_подтверждения_стирает_подпись(человек):
    номер = первый(человек, "SELECT id FROM meta.license LIMIT 1")
    человек.execute("UPDATE meta.license SET status = 'подтверждён' WHERE id = %s", (номер,))
    человек.execute("UPDATE meta.license SET status = 'черновик' WHERE id = %s", (номер,))
    кем, когда = человек.execute(
        "SELECT confirmed_by, confirmed_at FROM meta.license WHERE id = %s", (номер,)
    ).fetchone()
    assert кем is None and когда is None


# ---------------------------------------------------------------- выселение
def test_выселение_без_записки_не_проходит(агент):
    номер = первый(агент, "SELECT id FROM data.source_layer LIMIT 1")
    with pytest.raises(psycopg.errors.RaiseException):
        агент.execute("UPDATE data.source_layer SET evicted = true WHERE id = %s", (номер,))


def test_выселение_с_запиской_проходит(агент):
    номер = первый(агент, "SELECT id FROM data.source_layer LIMIT 1")
    агент.execute(
        """UPDATE data.source_layer
              SET evicted = true, evict_note = 'проба: перекачать из источника'
            WHERE id = %s""",
        (номер,),
    )
    assert агент.execute(
        "SELECT evicted FROM data.source_layer WHERE id = %s", (номер,)
    ).fetchone()[0]


# ------------------------------------------------------------ журнал решений
def test_отклонение_без_причины_не_записывается(человек):
    with pytest.raises(psycopg.errors.CheckViolation):
        человек.execute(
            """INSERT INTO meta.review (reviewer, object_kind, object_id, decision)
               VALUES ('проба', 'лицензия', 1, 'отклонён')"""
        )


def test_отклонение_с_причиной_записывается(человек):
    человек.execute(
        """INSERT INTO meta.review (reviewer, object_kind, object_id, decision, comment)
           VALUES ('проба', 'лицензия', 1, 'отклонён', 'условия не позволяют публикацию')"""
    )
    assert человек.execute(
        "SELECT count(*) FROM meta.review WHERE reviewer = 'проба'"
    ).fetchone()[0] == 1
