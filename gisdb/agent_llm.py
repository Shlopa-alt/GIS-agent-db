r"""Единственная точка обращения к языковой модели во всём проекте.

Через этот модуль ходят все агенты базы: паспортизатор признаков, будущий
сборщик и разборщик литературы. Смысл единственной точки — переезд на
локальную модель: чтобы сменить исполнителя, правится настройка, а не
десяток скриптов.

| Переменная | По умолчанию | Что задаёт |
|---|---|---|
| `GIS_LLM_PROVIDER` | `локальный` | `облако`, `локальный`, `средство` или `авто` |
| `GIS_LLM_CLOUD_MODEL` | `claude-opus-5` | модель в облаке |
| `GIS_LLM_LOCAL_URL` | `http://127.0.0.1:8080/v1` | адрес локального сервера |
| `GIS_LLM_LOCAL_MODEL` | `локальная` | имя модели на локальном сервере |
| `GIS_LLM_CLOUD_URL` | `https://api.anthropic.com` | адрес облака |
| `GIS_LLM_CONTEXT_TOKENS` | `16000` | предел задачи, чтобы влезала в локальную |
| `GIS_LLM_CLI` | `claude` | средство разработки как исполнитель |
| `GIS_LLM_CLI_MODEL` | `claude-opus-5` | модель, которую средство спрашивает |
| `ANTHROPIC_API_KEY` | — | иначе ключ читается из `secrets/anthropic.key` |

`авто` означает: локальный сервер, если отвечает; иначе облако, если есть
ключ; иначе установленное средство разработки. Как только на машине
поднимется свой сервер, агенты перейдут на него сами.

Третий исполнитель — **средство разработки** (Claude Code) — временная
подпорка на случай, когда ключа к облаку нет, а работу надо делать: запрос
уходит тем же ключом, которым разработчик и так пользуется. Схема ответа там
не навязывается интерфейсом, поэтому она пишется в роль текстом, а годность
ответа решает та же :func:`check_schema`. Для рабочего сервера этот путь не
годится: средство разработки на сервере не стоит.

Правила, одинаковые для обеих моделей и заложенные здесь, а не в скриптах:

* **ответ только по схеме.** В облаке схема навязывается инструментом, на
  локальном сервере — полем `response_format`; сверх этого ответ проверяется
  своей проверкой :func:`check_schema`, потому что маленькая модель схему
  нарушает даже когда сервер обещает обратное;
* **повторная попытка с указанием на ошибку.** Число попыток пишется в
  журнал: это прямая мера пригодности модели, а не служебная мелочь;
* **предел контекста.** Задача длиннее `GIS_LLM_CONTEXT_TOKENS` не
  отправляется никуда, даже в облако — иначе к локальной модели переедет
  код, который на ней физически не работает;
* **всё обращение пишется в `runs.llm_call`** (:func:`log_call`): модель,
  время ответа, токены, попытки, разобранный ответ.

Модель ничего не подтверждает: её ответ — черновик, статус «подтверждён»
ставит человек (триггер `meta.guard_confirmation`).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import db

class ОтказИсполнителя(RuntimeError):
    """Исполнитель не ответил по своей причине, а не из-за плохого вопроса.

    Предел обращений, перегруженный сервер, упавший процесс — всё это не
    говорит ничего ни о признаке, ни о модели, и не должно тратить попытки
    ответа по схеме. Такой отказ ждут, а когда ждать бесполезно — прогон
    останавливают целиком: перебирать очередь дальше бессмысленно.
    """


ОБЛАКО = "облако"
ЛОКАЛЬНЫЙ = "локальный"
СРЕДСТВО = "средство"     # установленное средство разработки, ключ разработчика

# Умолчание — локальная модель: работа с базой ведётся на своём сервере,
# облако и средство разработки включаются только явной настройкой.
PROVIDER: str = os.environ.get("GIS_LLM_PROVIDER", "локальный")
CLOUD_MODEL: str = os.environ.get("GIS_LLM_CLOUD_MODEL", "claude-opus-5")
# Где искать локальный сервер, когда адрес не задан: Ollama, llama.cpp,
# LM Studio — у каждого свой порт по умолчанию, а интерфейс у всех общий.
ЛОКАЛЬНЫЕ_АДРЕСА: tuple[str, ...] = (
    "http://127.0.0.1:11434/v1",    # Ollama
    "http://127.0.0.1:8080/v1",     # llama.cpp (llama-server)
    "http://127.0.0.1:1234/v1",     # LM Studio
)
LOCAL_URL: str = os.environ.get("GIS_LLM_LOCAL_URL", ЛОКАЛЬНЫЕ_АДРЕСА[0])
# Пустое имя означает «спросить у сервера»: на локальном сервере обычно стоит
# одна модель, и заставлять человека переписывать её имя в двух местах незачем.
LOCAL_MODEL: str = os.environ.get("GIS_LLM_LOCAL_MODEL", "")
CLOUD_URL: str = os.environ.get("GIS_LLM_CLOUD_URL", "https://api.anthropic.com")
CLI: str = os.environ.get("GIS_LLM_CLI", "claude")
CLI_MODEL: str = os.environ.get("GIS_LLM_CLI_MODEL", "claude-opus-5")
# Локальные модели нового поколения рассуждают вслух перед ответом. Для
# паспорта это плохая сделка: рассуждение идёт по-английски, стоит сотни
# токенов и минуты времени, а в базу попадает только конечный JSON. Пустое
# значение означает «не трогать настройку сервера».
РАССУЖДЕНИЕ: str = os.environ.get("GIS_LLM_REASONING", "none")
CONTEXT_TOKENS: int = int(os.environ.get("GIS_LLM_CONTEXT_TOKENS", "16000"))
MAX_ANSWER_TOKENS: int = int(os.environ.get("GIS_LLM_ANSWER_TOKENS", "1200"))

# Сколько ждать, когда исполнитель отказывает по пределу обращений, а не по
# существу ответа. Пределы у облака и у средства разработки снимаются сами —
# вопрос только во времени, поэтому ожидание растёт, а не сдаётся сразу.
ПАУЗЫ: tuple[int, ...] = (60, 180, 600, 1800, 3600)

# Имя инструмента, которым в облаке навязывается схема ответа.
ИНСТРУМЕНТ = "ответ"

# Средству разработки схему навязать нечем — она приписывается к роли текстом.
УКАЗАНИЕ = """

Ответ — один объект JSON по схеме, без пояснений, без разметки и без текста
вокруг:
"""


# ----------------------------------------------------------------- вспомогательное
def estimate_tokens(text: str) -> int:
    """Грубая оценка длины в токенах: русский текст — около трёх символов на токен.

    Точность здесь не нужна и вредна: оценка служит только предохранителем,
    чтобы задача, написанная под облако, не оказалась неподъёмной для
    локальной модели на 12 ГБ видеопамяти.
    """
    return len(text) // 3 + 1


def cloud_key() -> str:
    """Ключ облака: из окружения, иначе из файла секретов рядом с паролями базы."""
    from_env = os.environ.get("ANTHROPIC_API_KEY")
    if from_env:
        return from_env
    файл = db.SECRETS_DIR / "anthropic.key"
    if файл.exists():
        return файл.read_text(encoding="utf-8").strip()
    raise RuntimeError(
        "Нет ключа облачной модели: задайте ANTHROPIC_API_KEY или положите его "
        f"в {файл}. Локальная модель ключа не требует — см. GIS_LLM_PROVIDER."
    )


def local_client(timeout: float = 300.0):
    """Клиент для локального сервера — в обход системного прокси.

    На этой машине в системе прописан прокси (обычное дело при работе через
    туннель), и запрос к своей же петле по умолчанию уходит в него и виснет.
    `trust_env=False` отключает подхват прокси из окружения и реестра.
    """
    import httpx

    return httpx.Client(trust_env=False, timeout=timeout)


def local_alive(url: str = LOCAL_URL, timeout: float = 1.5) -> bool:
    """Отвечает ли локальный сервер моделей (llama.cpp, Ollama, LM Studio)."""
    return bool(local_models(url, timeout) is not None)


def local_models(url: str = LOCAL_URL, timeout: float = 1.5) -> list[str] | None:
    """Имена моделей на локальном сервере; None — сервер не отвечает."""
    try:
        with local_client(timeout) as клиент:
            ответ = клиент.get(f"{url}/models")
            if ответ.status_code >= 500:
                return None
            тело = ответ.json()
    except Exception:
        return None
    return [м.get("id", "") for м in тело.get("data", [])]


def local_digest(url: str = LOCAL_URL, model: str = "",
                 timeout: float = 1.5) -> str:
    """Отпечаток весов модели на локальном сервере; пусто — узнать нечем.

    Имя модели называет тег в реестре, а не конкретные веса: содержимое тега
    обновляется, и через полгода «тот же» прогон окажется сделан другой
    моделью. Отпечаток спрашивается у Ollama её собственным путём (в общем
    интерфейсе такого поля нет); другой сервер его не отдаст, и это не беда —
    столбец в журнале просто останется пустым.
    """
    try:
        with local_client(timeout) as клиент:
            тело = клиент.get(f"{url.removesuffix('/v1')}/api/tags").json()
    except Exception:
        return ""
    for м in тело.get("models", []):
        if м.get("model") == model or м.get("name") == model:
            return str(м.get("digest", ""))
    return ""


def локальный_адрес(timeout: float = 1.5) -> str | None:
    """Первый адрес, по которому нашёлся сервер моделей.

    Адрес из окружения проверяется единственным: если человек назвал его явно,
    подставлять вместо него другой сервер — значит тихо сменить исполнителя.
    """
    адреса = ((LOCAL_URL,) if os.environ.get("GIS_LLM_LOCAL_URL")
              else ЛОКАЛЬНЫЕ_АДРЕСА)
    return next((а for а in адреса if local_alive(а, timeout)), None)


def средство_доступно(команда: str = CLI) -> bool:
    """Стоит ли на машине средство разработки, которым можно спросить модель."""
    import shutil

    return shutil.which(команда) is not None


def ключ_есть() -> bool:
    """Есть ли ключ облака — без выбрасывания исключения, для автовыбора."""
    try:
        cloud_key()
        return True
    except RuntimeError:
        return False


# Слова, по которым исполнитель узнаётся в отказе по пределу обращений.
ПРИЗНАКИ_ПРЕДЕЛА = (
    "limit", "предел", "rate", "quota", "overloaded", "перегруж",
    "429", "529", "too many requests", "try again",
)


def предел_обращений(текст: str) -> bool:
    """Отказ ли это по пределу обращений (ждать), а не по существу (чинить)."""
    низом = текст.lower()
    return any(признак in низом for признак in ПРИЗНАКИ_ПРЕДЕЛА)


def check_schema(данные: Any, схема: dict) -> str | None:
    """Своя проверка ответа по схеме; возвращает текст ошибки или `None`.

    Проверяется то, на чём спотыкается маленькая модель: пропущенные поля,
    посторонние поля, значения вне перечня, пустые строки, число вместо
    строки. Вложенность глубже одного уровня в паспортах не нужна.
    """
    if not isinstance(данные, dict):
        return f"ожидался объект JSON, пришло {type(данные).__name__}"

    свойства = схема.get("properties", {})
    обязательные = схема.get("required", list(свойства))

    нет = [имя for имя in обязательные if имя not in данные]
    if нет:
        return "не хватает полей: " + ", ".join(нет)

    лишние = [имя for имя in данные if имя not in свойства]
    if лишние:
        return "посторонние поля: " + ", ".join(лишние)

    for имя, значение in данные.items():
        правило = свойства[имя]
        тип = правило.get("type")
        if тип == "string":
            if not isinstance(значение, str) or not значение.strip():
                return f"поле «{имя}» должно быть непустой строкой"
            if "enum" in правило and значение not in правило["enum"]:
                return (f"поле «{имя}»: значение «{значение}» вне перечня "
                        + ", ".join(правило["enum"]))
            if len(значение) < правило.get("minLength", 0):
                return f"поле «{имя}» короче {правило['minLength']} символов"
        elif тип == "boolean" and not isinstance(значение, bool):
            return f"поле «{имя}» должно быть true или false"
        elif тип in ("number", "integer") and isinstance(значение, bool):
            return f"поле «{имя}» должно быть числом"
    return None


def _first_json(текст: str) -> Any:
    """Достать объект JSON из ответа, обрамлённого пояснениями или разметкой.

    Локальные модели любят добавить «вот ответ:» и обрамление ```json —
    облачная так почти не делает, но код общий.
    """
    текст = текст.strip()
    текст = re.sub(r"^```(?:json)?|```$", "", текст, flags=re.MULTILINE).strip()
    try:
        return json.loads(текст)
    except json.JSONDecodeError:
        pass
    начало = текст.find("{")
    if начало < 0:
        raise ValueError("в ответе нет объекта JSON")
    глубина = 0
    for i, символ in enumerate(текст[начало:], начало):
        глубина += (символ == "{") - (символ == "}")
        if глубина == 0:
            return json.loads(текст[начало:i + 1])
    raise ValueError("объект JSON в ответе не закрыт")


# ------------------------------------------------------------------------ ответ
@dataclass
class Answer:
    """Разобранный ответ модели вместе с тем, чем он обошёлся."""

    data: dict
    provider: str
    model: str
    in_tokens: int = 0
    out_tokens: int = 0
    attempts: int = 1
    latency_ms: int = 0
    context_chars: int = 0
    errors: list[str] = field(default_factory=list)  # ошибки неудавшихся попыток
    digest: str = ""                                 # отпечаток весов, если есть


# ------------------------------------------------------------------------ модель
class Model:
    """Модель-исполнитель: облачная или локальная, снаружи — одинаковая.

    >>> модель = Model()                       # выбор по настройке
    >>> модель.ask(роль, задача, схема).data   # dict по схеме
    """

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        context_tokens: int = CONTEXT_TOKENS,
        temperature: float = 0.0,
        local_url: str = LOCAL_URL,
    ) -> None:
        provider = provider or PROVIDER
        if provider == "авто":
            найден = локальный_адрес()
            if найден:
                provider, local_url = ЛОКАЛЬНЫЙ, найден
            elif ключ_есть():
                provider = ОБЛАКО
            elif средство_доступно():
                provider = СРЕДСТВО
            else:
                raise RuntimeError(
                    "исполнителя нет: локальный сервер не отвечает, ключа облака "
                    f"нет ({db.SECRETS_DIR / 'anthropic.key'} или ANTHROPIC_API_KEY), "
                    f"средство разработки «{CLI}» не найдено"
                )
        if provider not in (ОБЛАКО, ЛОКАЛЬНЫЙ, СРЕДСТВО):
            raise ValueError(f"неизвестный провайдер: {provider}")

        self.provider = provider
        self.model = model or {
            ОБЛАКО: CLOUD_MODEL, ЛОКАЛЬНЫЙ: LOCAL_MODEL, СРЕДСТВО: CLI_MODEL,
        }[provider]
        if provider == ЛОКАЛЬНЫЙ and not self.model:
            # Имя модели спрашиваем у сервера: в журнале прогона должно стоять,
            # чем именно сделан паспорт, а не безличное «локальная».
            имена = local_models(local_url) or []
            if not имена:
                raise RuntimeError(
                    f"локальный сервер {local_url} не назвал ни одной модели: "
                    "скачайте её (например, `ollama pull gemma4:12b`) или "
                    "задайте GIS_LLM_LOCAL_MODEL"
                )
            self.model = имена[0]
        self.context_tokens = context_tokens
        self.temperature = temperature
        self.local_url = local_url
        self._client = None
        self._digest: str | None = None

    def __repr__(self) -> str:  # для журнала прогона
        return f"{self.provider}:{self.model}"

    @property
    def digest(self) -> str:
        """Отпечаток весов — спрашивается один раз за жизнь объекта."""
        if self._digest is None:
            self._digest = (local_digest(self.local_url, self.model)
                            if self.provider == ЛОКАЛЬНЫЙ else "")
        return self._digest

    # ------------------------------------------------------------------ запрос
    def ask(self, role: str, task: str, schema: dict, attempts: int = 2) -> Answer:
        """Задать вопрос и получить ответ по схеме.

        `role` — кто отвечает и по каким правилам, `task` — сами факты и
        вопрос, `schema` — JSON Schema ответа. Повторная попытка делается с
        добавленным указанием на ошибку разбора: так слабая модель обычно
        исправляется со второго раза, а её слабость остаётся видна в журнале.
        """
        длина = estimate_tokens(role) + estimate_tokens(task)
        if длина > self.context_tokens:
            raise ValueError(
                f"задача ~{длина} токенов при пределе {self.context_tokens}: "
                "сократите факты, иначе на локальной модели это не заработает"
            )

        замечание = ""
        ошибки: list[str] = []
        ожиданий = 0
        начало = time.monotonic()
        попытка = 0
        while попытка < attempts:
            попытка += 1
            текст = task if not замечание else f"{task}\n\nПрошлый ответ отклонён: {замечание}"
            try:
                спросить = {
                    ОБЛАКО: self._ask_cloud,
                    ЛОКАЛЬНЫЙ: self._ask_local,
                    СРЕДСТВО: self._ask_tool,
                }[self.provider]
                сырое, вход, выход = спросить(role, текст, schema)
                беда = check_schema(сырое, schema)
                if беда is None:
                    return Answer(
                        data=сырое, provider=self.provider, model=self.model,
                        in_tokens=вход, out_tokens=выход, attempts=попытка,
                        latency_ms=int((time.monotonic() - начало) * 1000),
                        context_chars=len(role) + len(task), errors=ошибки,
                        digest=self.digest,
                    )
                замечание = беда
            except ОтказИсполнителя as отказ:
                # Отказ по пределу — не вина вопроса: попытка не расходуется.
                попытка -= 1
                if ожиданий >= len(ПАУЗЫ):
                    raise
                пауза = ПАУЗЫ[ожиданий]
                ожиданий += 1
                print(f"  {self}: {отказ}; жду {пауза} с ({ожиданий} из "
                      f"{len(ПАУЗЫ)})", flush=True)
                ошибки.append(f"ожидание {пауза} с: {отказ}")
                time.sleep(пауза)
                continue
            except (ValueError, json.JSONDecodeError) as ошибка:
                замечание = str(ошибка)
            ошибки.append(замечание)

        raise RuntimeError(
            f"{self}: ответ по схеме не получен за {attempts} попыт(ки): "
            + "; ".join(ошибки)
        )

    # ----------------------------------------------------------------- облако
    def _ask_cloud(self, role: str, task: str, schema: dict) -> tuple[Any, int, int]:
        """Схема навязывается инструментом: модель обязана вызвать его и только его."""
        import anthropic

        if self._client is None:
            # Адрес задаётся явно: переменная ANTHROPIC_BASE_URL принадлежит
            # средству разработки и не должна уводить агента проекта в чужой
            # прокси. Свой адрес задаётся через GIS_LLM_CLOUD_URL.
            self._client = anthropic.Anthropic(api_key=cloud_key(), base_url=CLOUD_URL)

        import anthropic as _a

        try:
            ответ = self._create_cloud(role, task, schema)
        except (_a.RateLimitError, _a.APIStatusError, _a.APIConnectionError) as сбой:
            раз = getattr(сбой, "status_code", None)
            if isinstance(сбой, _a.APIConnectionError) or раз in (408, 429, 500, 502, 503, 529):
                raise ОтказИсполнителя(f"облако: {сбой}") from сбой
            raise
        for блок in ответ.content:
            if блок.type == "tool_use":
                return блок.input, ответ.usage.input_tokens, ответ.usage.output_tokens
        raise ValueError("модель не заполнила схему ответа")

    def _create_cloud(self, role: str, task: str, schema: dict):
        """Сам запрос к облаку, отделённый ради разбора отказов выше."""
        return self._client.messages.create(
            model=self.model,
            max_tokens=MAX_ANSWER_TOKENS,
            temperature=self.temperature,
            system=role,
            messages=[{"role": "user", "content": task}],
            tools=[{
                "name": ИНСТРУМЕНТ,
                "description": "Единственный способ ответить: заполнить поля схемы",
                "input_schema": schema,
            }],
            tool_choice={"type": "tool", "name": ИНСТРУМЕНТ},
        )

    # -------------------------------------------------------------- локальная
    def _ask_local(self, role: str, task: str, schema: dict) -> tuple[Any, int, int]:
        """Локальный сервер с совместимым интерфейсом: llama.cpp, Ollama, LM Studio.

        Схема передаётся полем `response_format`; сервер, который его не
        понимает, просто вернёт свободный текст — тогда объект достаётся
        разбором, а негодный ответ отсеет :func:`check_schema`.
        """
        запрос = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": MAX_ANSWER_TOKENS,
            "messages": [
                {"role": "system", "content": role},
                {"role": "user", "content": task},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": ИНСТРУМЕНТ, "schema": schema, "strict": True},
            },
        }
        if РАССУЖДЕНИЕ:
            # Сервер, который поля не знает, его просто не заметит.
            запрос["reasoning_effort"] = РАССУЖДЕНИЕ
        if self._client is None:
            self._client = local_client()
        import httpx

        try:
            ответ = self._client.post(f"{self.local_url}/chat/completions", json=запрос)
            ответ.raise_for_status()
        except httpx.HTTPStatusError as сбой:
            if сбой.response.status_code in (408, 429, 500, 502, 503):
                raise ОтказИсполнителя(f"локальный сервер: {сбой}") from сбой
            raise
        except httpx.TransportError as сбой:
            raise ОтказИсполнителя(f"локальный сервер не отвечает: {сбой}") from сбой
        тело = ответ.json()
        расход = тело.get("usage") or {}
        return (
            _first_json(тело["choices"][0]["message"]["content"]),
            int(расход.get("prompt_tokens", 0)),
            int(расход.get("completion_tokens", 0)),
        )


    # ------------------------------------------------- средство разработки
    def _ask_tool(self, role: str, task: str, schema: dict) -> tuple[Any, int, int]:
        """Спросить модель через установленное средство разработки.

        Средство запускается разовым вызовом без своих инструментов и без своей
        роли: `--tools ""` и `--system-prompt` оставляют от него только канал к
        модели. Схему навязать нечем, поэтому она приписана к роли текстом —
        дальше ответ разбирается и проверяется так же, как у локальной модели.
        """
        import subprocess

        роль = role + УКАЗАНИЕ + json.dumps(schema, ensure_ascii=False)
        команда = [
            CLI, "-p", "--model", self.model, "--system-prompt", роль,
            "--tools", "", "--max-turns", "1",
            "--output-format", "json", "--no-session-persistence",
        ]
        готово = subprocess.run(
            команда, input=task.encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600,
        )
        if готово.returncode != 0:
            # Средство молчит в stderr, когда упирается в предел обращений;
            # причина остаётся в stdout, поэтому смотрим оба потока. Ненулевой
            # код — это всегда отказ исполнителя: до модели вопрос не дошёл.
            беда = (готово.stderr.decode("utf-8", "replace").strip()
                    or готово.stdout.decode("utf-8", "replace").strip()
                    or "без объяснения (обычно так выглядит предел обращений)")
            raise ОтказИсполнителя(
                f"средство разработки вернуло код {готово.returncode}: {беда[:300]}"
            )
        тело = json.loads(готово.stdout.decode("utf-8"))
        if тело.get("is_error"):
            беда = str(тело.get("result"))[:300]
            if предел_обращений(беда):
                raise ОтказИсполнителя(беда)
            raise ValueError(беда)
        расход = тело.get("usage") or {}
        return (
            _first_json(тело["result"]),
            int(расход.get("input_tokens", 0)) + int(расход.get("cache_read_input_tokens", 0)),
            int(расход.get("output_tokens", 0)),
        )


# --------------------------------------------------------------------- журнал
def log_call(
    conn,
    run_id: int | None,
    answer: Answer | None,
    task: str,
    subject: str,
    model: Model | None = None,
    error: str | None = None,
) -> None:
    """Записать обращение к модели в `runs.llm_call` — удачное или нет.

    Неудачные важнее удачных: по ним видно, на чём именно спотыкается
    модель, и сколько таких мест прибавится при переходе на локальную.
    """
    провайдер = answer.provider if answer else (model.provider if model else "неизвестен")
    имя = answer.model if answer else (model.model if model else "неизвестна")
    отпечаток = answer.digest if answer else (model.digest if model else "")
    conn.execute(
        """INSERT INTO runs.llm_call
               (run_id, provider, model, model_digest, task, subject,
                context_chars, in_tokens, out_tokens, latency_ms, attempts,
                ok, error, answer)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            run_id, провайдер, имя, отпечаток or None, task, subject,
            answer.context_chars if answer else None,
            answer.in_tokens if answer else None,
            answer.out_tokens if answer else None,
            answer.latency_ms if answer else None,
            answer.attempts if answer else 1,
            error is None,
            error if error else ("; ".join(answer.errors) or None if answer else None),
            json.dumps(answer.data, ensure_ascii=False) if answer else None,
        ),
    )
