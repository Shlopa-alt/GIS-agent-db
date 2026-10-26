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


from conftest import соединение  # noqa: E402  — одна попытка на весь прогон


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


@pytest.mark.parametrize("таблица, ключ", [
    ("meta.license", "id"),
    ("meta.feature_passport", "feature_id"),
    ("kb.concept", "id"),
])
def test_агент_не_отклоняет(агент, таблица, ключ):
    # Отклонение — такой же вердикт: без строки в meta.review у него нет причины.
    номер = первый(агент, f"SELECT {ключ} FROM {таблица} LIMIT 1")
    with pytest.raises(psycopg.errors.RaiseException):
        агент.execute(
            f"UPDATE {таблица} SET status = 'отклонён' WHERE {ключ} = %s", (номер,)
        )


@pytest.mark.parametrize("таблица, ключ", [
    ("meta.license", "id"),
    ("meta.feature_passport", "feature_id"),
    ("kb.concept", "id"),
])
def test_агент_не_снимает_вердикт(агент, таблица, ключ):
    """Снятие вердикта — тоже вердикт (миграция 019).

    Прежний запрет смотрел только на новое значение статуса, и переход
    «подтверждён» → «черновик» выглядел по нему безобидным. Агент возвращал
    подтверждённую строку в черновик, подпись человека обнулялась тем же
    триггером, а в `meta.review` не ложилось ничего: журнал ведёт панель, а
    агент ходит мимо неё. Решение человека стиралось без следа.
    """
    if "TG_OP" not in агент.execute(
        "SELECT pg_get_functiondef('meta.guard_confirmation'::regproc)"
    ).fetchone()[0]:
        pytest.skip("миграция 019_verdict_removal.sql не накатана")
    номер = первый(
        агент, f"SELECT {ключ} FROM {таблица} WHERE status = 'подтверждён' LIMIT 1")
    with pytest.raises(psycopg.errors.RaiseException, match="снимает только человек"):
        агент.execute(
            f"UPDATE {таблица} SET status = 'черновик' WHERE {ключ} = %s", (номер,)
        )


def test_человек_возвращает_на_доработку(человек, monkeypatch):
    """Законный путь снятия вердикта: человек, кнопка в панели, строка в журнале.

    Он нужен не теоретически: 47 паспортов в базе описаны не локальной моделью,
    и переписать их иначе нельзя — паспортизатор берёт только черновики.
    """
    from gisdb.panel import queries as q

    номер = первый(
        человек,
        "SELECT feature_id FROM meta.feature_passport WHERE status = 'подтверждён' LIMIT 1")
    monkeypatch.setattr(человек, "commit", lambda: None)

    q.применить(человек, q.ВИДЫ["признаки"], [номер], "черновик", "проверка", {},
                "описано не локальной моделью", 5)

    статус, подпись = человек.execute(
        "SELECT status, confirmed_by FROM meta.feature_passport WHERE feature_id = %s",
        (номер,),
    ).fetchone()
    решение, причина = человек.execute(
        """SELECT decision, comment FROM meta.review
            WHERE object_kind = 'паспорт признака' AND object_id = %s
            ORDER BY at DESC LIMIT 1""", (номер,)
    ).fetchone()
    assert (статус, подпись) == ("черновик", None), "вердикт не снят"
    assert решение == "черновик" and причина, "возврат на доработку без причины в журнале"


def test_человек_подтверждает_и_подписывается(человек):
    номер = первый(человек, "SELECT id FROM meta.license LIMIT 1")
    # Запись могла быть подтверждена человеком раньше — тогда подпись уже стоит
    # его именем, и проверять было бы нечего. Снятие подтверждения возвращает
    # строку в исходное состояние; соединение всё равно откатывается.
    человек.execute("UPDATE meta.license SET status = 'черновик' WHERE id = %s", (номер,))
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


def test_артефакт_прогона_пишется_с_отпечатком(агент):
    """Файл, произведённый прогоном, должен записываться вместе с хешем.

    `runs.artifact` пустовала всё время работы базы: прогоны писали, из чего
    сделаны, но не что из них вышло. Копия базы без записи о том, чем и когда
    она сделана, доказательной силы не имеет — проверяем обе половины.
    """
    from pathlib import Path

    from gisdb import db_ingest as ingest

    прогон = агент.execute(
        "INSERT INTO runs.run (script, note) VALUES ('проба', 'проба') RETURNING id"
    ).fetchone()[0]
    хеш = ingest.add_artifact(агент, прогон, Path(__file__), "проба",
                              "этот же файл проверки")
    путь, записанный = агент.execute(
        "SELECT path, checksum FROM runs.artifact WHERE run_id = %s", (прогон,)
    ).fetchone()
    assert записанный == хеш == ingest.checksum(Path(__file__))
    # Файл проверки лежит в репозитории базы — значит и префикс его, «база:/».
    assert путь.startswith("база:/"), f"путь без буквы диска ожидался, а стоит {путь}"


# ------------------------------------------------- согласие кода с живой базой
def test_учёт_миграций_не_отстаёт(читатель):
    """Код требует схемы, которой в базе может не быть — это должно быть видно.

    Проверка общая, не про одну миграцию: когда в репозитории появляется файл
    схемы, а на живой базе он не накатан, половина кода работает, половина
    отказывает — и узнаётся это случайно. Здесь расхождение названо сразу.
    """
    from pathlib import Path

    применены = {имя for (имя,) in читатель.execute(
        "SELECT filename FROM meta.migration").fetchall()}
    схема = Path(__file__).resolve().parents[1] / "db" / "sql"
    ждут = [п.name for п in sorted(схема.glob("*.sql")) if п.name not in применены]
    assert not ждут, ("база отстала от кода, не накатаны: " + ", ".join(ждут)
                      + ". Накатить: python db/migrate.py")


def test_сбор_фактов_читает_паспорт(читатель):
    """Запрос фактов о признаке должен работать на живой базе целиком.

    Он собирает паспорт, словарь и статистику одним запросом по пяти таблицам;
    отсутствие любой из них ломает карточку признака в панели, а не отдельное
    поле. Проверяется на подтверждённом паспорте — такие в базе есть всегда.
    """
    from gisdb import feature_facts

    строки = feature_facts.выбрать(читатель, "WHERE p.status = %s", ("подтверждён",))
    assert строки, "в базе нет подтверждённых паспортов — проверять нечего"
    ф = feature_facts.факты(читатель, строки[0])
    assert ф["техническое"], "техническое описание не собралось"
    assert ф["группа"], "группа признака не определилась"


# --------------------------------------------------------- вид наружу (схема api)
def test_наружу_не_уходят_черновики(читатель):
    """Схема `api` показывает только подтверждённое человеком.

    Черновик — незаконченная работа агента. Если он уйдёт в выгрузку, читающий
    примет его за знание базы: подписи под ним нет, а выглядит он так же.
    """
    for вид in ("feature", "layer", "concept", "feature_concept", "citation"):
        строки = читатель.execute(
            f"SELECT count(*) FROM api.{вид} WHERE подтвердил IS NULL").fetchone()[0]
        assert строки == 0, f"api.{вид}: {строки} строк без подписи проверяющего"


def test_права_распространения_отвечают_по_каждому_признаку(читатель):
    """Без ответа «можно ли отдавать» выгрузка — вопрос, заданный в пустоту."""
    всего, без_ответа = читатель.execute(
        """SELECT count(*), count(*) FILTER (WHERE слоёв_источников > 0
                                               AND можно_распространять IS NULL)
             FROM api.redistribution"""
    ).fetchone()
    assert всего > 0, "в api.redistribution нет ни одного признака"
    assert без_ответа == 0, (
        f"{без_ответа} признаков со слоями-источниками без ответа о правах: "
        "лицензия слоя не заполнена или не подтверждена"
    )


def test_запрещённый_слой_запрещает_признак(читатель):
    """Правило, ради которого всё это: один запрещённый источник решает за всех.

    Значения признака несут в себе содержимое слоя, пусть и свёрнутое в
    ячейку, — поэтому «разрешено» требует разрешения от каждого источника.
    """
    спорные = читатель.execute(
        """SELECT р.признак FROM api.redistribution р
             JOIN data.feature f ON f.code = р.признак
             JOIN meta.derivation d ON d.target_kind = 'признак'
                                   AND d.target_id = f.id AND d.source_kind = 'слой'
             JOIN meta.layer_passport lp ON lp.layer_id = d.source_id
             JOIN meta.license lic ON lic.id = lp.license_id
            WHERE р.можно_распространять AND NOT lic.allows_redistribution
            LIMIT 5"""
    ).fetchall()
    assert not спорные, ("признаки разрешены к передаче, хотя источник запрещён: "
                         + ", ".join(п for (п,) in спорные))
