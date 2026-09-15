"""Единая точка обращения к модели: разбор ответа и поведение при отказе.

Проверяется то, из-за чего прогон 14.09.2026 потерял 58 признаков из 85: отказ
исполнителя по пределу обращений считался плохим ответом и тратил попытки.
Отказ и негодный ответ — разные вещи: первый ждут, второй исправляют.

Сеть здесь не нужна: исполнитель подменяется.
"""
import pytest

from gisdb import agent_llm as м

СХЕМА = {
    "type": "object",
    "properties": {
        "смысл": {"type": "string", "minLength": 5},
        "уверенность": {"type": "string", "enum": ["высокая", "средняя", "низкая"]},
    },
    "required": ["смысл", "уверенность"],
    "additionalProperties": False,
}
ОТВЕТ = {"смысл": "содержательный ответ", "уверенность": "средняя"}


class Подставная(м.Model):
    """Модель без сети: отдаёт заготовленные ответы и считает обращения."""

    def __init__(self, ответы):
        super().__init__(provider=м.ЛОКАЛЬНЫЙ, model="подставная")
        self.ответы = list(ответы)
        self.обращений = 0

    def _ask_local(self, role, task, schema):
        self.обращений += 1
        что = self.ответы.pop(0) if self.ответы else ОТВЕТ
        if isinstance(что, Exception):
            raise что
        return что, 10, 5


@pytest.fixture(autouse=True)
def без_ожидания(monkeypatch):
    """В проверке ждать нечего: ступени ожидания обнуляются, число сохраняется."""
    monkeypatch.setattr(м, "ПАУЗЫ", (0, 0))


# ----------------------------------------------------------- проверка схемы
@pytest.mark.parametrize("ответ, кусок_жалобы", [
    ({"смысл": "содержательный ответ"}, "не хватает полей"),
    ({**ОТВЕТ, "лишнее": 1}, "посторонние поля"),
    ({**ОТВЕТ, "уверенность": "абсолютная"}, "вне перечня"),
    ({**ОТВЕТ, "смысл": "   "}, "непустой строкой"),
    ({**ОТВЕТ, "смысл": "кор"}, "короче"),
    ("не объект", "ожидался объект"),
])
def test_негодный_ответ_называется_своим_именем(ответ, кусок_жалобы):
    assert кусок_жалобы in м.check_schema(ответ, СХЕМА)


def test_годный_ответ_принимается():
    assert м.check_schema(ОТВЕТ, СХЕМА) is None


@pytest.mark.parametrize("текст", [
    '{"а": 1}',
    'вот ответ:\n```json\n{"а": 1}\n```',
    'Пояснение сверху. {"а": 1} и хвост после.',
])
def test_объект_достаётся_из_болтливого_ответа(текст):
    assert м._first_json(текст) == {"а": 1}


# ---------------------------------------------------------------- отказы
def test_отказ_исполнителя_не_тратит_попытку():
    модель = Подставная([м.ОтказИсполнителя("usage limit reached"), ОТВЕТ])
    ответ = модель.ask("роль", "задача", СХЕМА)
    assert модель.обращений == 2
    assert ответ.attempts == 1                 # попытка ответа по-прежнему первая
    assert "ожидание" in ответ.errors[0]


def test_бесконечный_отказ_поднимается_наверх():
    модель = Подставная([м.ОтказИсполнителя("429 too many requests")] * 9)
    with pytest.raises(м.ОтказИсполнителя):
        модель.ask("роль", "задача", СХЕМА)
    assert модель.обращений == len(м.ПАУЗЫ) + 1  # столько же ожиданий, сколько ступеней


def test_негодный_ответ_тратит_попытки():
    модель = Подставная([{"смысл": "кор", "уверенность": "средняя"}] * 4)
    with pytest.raises(RuntimeError) as беда:
        модель.ask("роль", "задача", СХЕМА, attempts=2)
    assert модель.обращений == 2
    assert "короче" in str(беда.value)


def test_со_второй_попытки_исправился():
    модель = Подставная([{"смысл": "кор", "уверенность": "средняя"}, ОТВЕТ])
    ответ = модель.ask("роль", "задача", СХЕМА)
    assert ответ.attempts == 2 and ответ.data == ОТВЕТ


@pytest.mark.parametrize("текст, предел", [
    ("usage limit reached", True),
    ("429 too many requests", True),
    ("Server overloaded", True),
    ("модель не заполнила схему ответа", False),
    ("поле «смысл» короче 80 символов", False),
])
def test_отказ_по_пределу_узнаётся(текст, предел):
    assert м.предел_обращений(текст) is предел


# -------------------------------------------------------- предохранитель
def test_слишком_длинная_задача_никуда_не_уходит():
    модель = Подставная([])
    with pytest.raises(ValueError, match="токенов при пределе"):
        модель.ask("роль", "я" * (модель.context_tokens * 4), СХЕМА)
    assert модель.обращений == 0


# ------------------------------------------------- поиск локального сервера
@pytest.fixture
def сервер(monkeypatch):
    """Подставной набор живых адресов: сеть не трогаем."""
    def отвечают(живые: dict[str, list[str]]):
        monkeypatch.setattr(м, "local_models",
                            lambda url=м.LOCAL_URL, timeout=1.5: живые.get(url))
        monkeypatch.setattr(м, "local_alive",
                            lambda url=м.LOCAL_URL, timeout=1.5: url in живые)
    return отвечают


def test_сервер_ищется_по_известным_адресам(сервер, monkeypatch):
    monkeypatch.delenv("GIS_LLM_LOCAL_URL", raising=False)
    сервер({м.ЛОКАЛЬНЫЕ_АДРЕСА[1]: ["gemma4:12b"]})
    assert м.локальный_адрес() == м.ЛОКАЛЬНЫЕ_АДРЕСА[1]


def test_заданный_адрес_не_подменяется_другим(сервер, monkeypatch):
    """Назвал человек адрес — значит, этот, а не первый попавшийся сервер."""
    monkeypatch.setenv("GIS_LLM_LOCAL_URL", м.LOCAL_URL)
    сервер({а: ["модель"] for а in м.ЛОКАЛЬНЫЕ_АДРЕСА[1:]})
    assert м.локальный_адрес() is None


def test_молчащий_сервер_не_находится(сервер, monkeypatch):
    monkeypatch.delenv("GIS_LLM_LOCAL_URL", raising=False)
    сервер({})
    assert м.локальный_адрес() is None


def test_имя_модели_берётся_у_сервера(сервер):
    """В журнале прогона должно стоять имя модели, а не «локальная»."""
    сервер({м.LOCAL_URL: ["gemma4:12b", "qwen3.5:9b"]})
    модель = м.Model(provider=м.ЛОКАЛЬНЫЙ, model="")
    assert (модель.provider, модель.model) == (м.ЛОКАЛЬНЫЙ, "gemma4:12b")


def test_сервер_без_моделей_объясняет_что_делать(сервер):
    сервер({м.LOCAL_URL: []})
    with pytest.raises(RuntimeError, match="ollama pull"):
        м.Model(provider=м.ЛОКАЛЬНЫЙ, model="")


def test_авто_выбирает_найденный_локальный_сервер(сервер, monkeypatch):
    monkeypatch.delenv("GIS_LLM_LOCAL_URL", raising=False)
    monkeypatch.setattr(м, "LOCAL_MODEL", "")      # имя спрашиваем у сервера
    сервер({м.ЛОКАЛЬНЫЕ_АДРЕСА[2]: ["своя-модель"]})
    модель = м.Model(provider="авто")
    assert модель.local_url == м.ЛОКАЛЬНЫЕ_АДРЕСА[2]
    assert repr(модель) == "локальный:своя-модель"


# --------------------------------------------------------- отпечаток модели
class ПодставнойКлиент:
    """Ответ сервера без сети: отдаёт заготовленное тело на любой запрос."""

    def __init__(self, тело):
        self.тело, self.куда = тело, None

    def __enter__(self):
        return self

    def __exit__(self, *беда):
        return False

    def get(self, url):
        self.куда = url
        return type("Ответ", (), {"json": lambda _=None, т=self.тело: т})()


def test_отпечаток_берётся_у_сервера(monkeypatch):
    клиент = ПодставнойКлиент({"models": [
        {"model": "чужая", "digest": "нет"},
        {"model": "gemma4:12b", "digest": "4eb23ef1"},
    ]})
    monkeypatch.setattr(м, "local_client", lambda timeout=1.5: клиент)
    assert м.local_digest("http://127.0.0.1:11434/v1", "gemma4:12b") == "4eb23ef1"
    # Путь Ollama, а не общий: «/v1» отрезается.
    assert клиент.куда == "http://127.0.0.1:11434/api/tags"


def test_незнакомая_модель_остаётся_без_отпечатка(monkeypatch):
    monkeypatch.setattr(м, "local_client",
                        lambda timeout=1.5: ПодставнойКлиент({"models": []}))
    assert м.local_digest("http://127.0.0.1:11434/v1", "gemma4:12b") == ""


def test_сервер_без_такого_пути_не_роняет_прогон(monkeypatch):
    """У llama.cpp и LM Studio отпечатка нет — это не повод падать."""
    def отказ(timeout=1.5):
        raise OSError("нет такого пути")
    monkeypatch.setattr(м, "local_client", отказ)
    assert м.local_digest() == ""


def test_ответ_несёт_отпечаток_модели(monkeypatch):
    monkeypatch.setattr(м, "local_digest", lambda url="", model="", timeout=1.5: "abc123")
    ответ = Подставная([]).ask("роль", "задача", СХЕМА)
    assert ответ.digest == "abc123"


def test_отпечаток_спрашивается_один_раз(monkeypatch):
    спросов = []
    monkeypatch.setattr(м, "local_digest",
                        lambda url="", model="", timeout=1.5: спросов.append(1) or "abc")
    модель = Подставная([])
    модель.ask("роль", "задача", СХЕМА)
    модель.ask("роль", "задача", СХЕМА)
    assert len(спросов) == 1
