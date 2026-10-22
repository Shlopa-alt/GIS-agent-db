"""Клиент к базе знаний: обращения, отказы и неполные ответы.

База знаний подменяется маленьким сервером в этом же процессе — ни сети наружу,
ни живой базы, ни модели векторов не нужно. Проверяется ровно то, из-за чего
прежний мост молча ломался: интерфейс той машины меняется, и клиент обязан
показывать, чего он не получил, а не додумывать.
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gisdb import kb_client as к  # noqa: E402

ФРАГМЕНТ = {
    "doc_id": "10.1_a", "ord": 7, "title": "Гидротермальные изменения зоны",
    "year": 2021, "journal": "Руды и металлы", "url": "https://x/a.pdf",
    "authors": ["Иванов И.И."], "headings": ["Результаты"], "pages": [14, 15],
    "text": "Ранний калиевый метасоматизм сменяется окварцеванием.",
    "found_by": "оба", "score": 0.5, "similarity": 0.71,
}
ЧУЖОЙ = {**ФРАГМЕНТ, "doc_id": "10.2_b", "title": "Совсем другая статья"}

ДАТАСЕТ = {
    "name": "Билляхская зона", "includes": [], "documents": 2, "in_dictionary": True,
    "facts": [{
        "about": "оруденение", "direction": "→", "relation": "контролируется",
        "other": "Билляхская зона", "kind": "структура", "documents": 2, "fragments": 3,
        "chunk_ids": [11, 12],
        "quotes": [{"quote": "Оруденение контролируется Билляхской зоной.",
                    "doc_id": "10.1_a", "title": "Гидротермальные изменения зоны",
                    "year": 2021, "url": "https://x/a.pdf", "chunk_id": 11,
                    "pages": [27], "doi": "10.1/a"}],
    }],
}


class _БазаЗнаний(BaseHTTPRequestHandler):
    """Подставная база знаний. `с_правками` — та, где уже применены правки из
    инструкции: есть /api/health, страницы у цитат и фильтр по статье."""

    с_правками = True
    запросы: list[str] = []

    def log_message(self, *args):
        pass

    def _json(self, код, тело):
        данные = json.dumps(тело, ensure_ascii=False).encode("utf-8")
        self.send_response(код)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(данные)))
        self.end_headers()
        self.wfile.write(данные)

    def do_GET(self):  # noqa: N802 — имя задано базовым классом
        адрес = urlparse(self.path)
        ключи = {к_: з[0] for к_, з in parse_qs(адрес.query).items()}
        type(self).запросы.append(self.path)

        if адрес.path == "/api/health":
            if not type(self).с_правками:
                self._json(404, {"error": "нет такой страницы"})
                return
            self._json(200, {"documents": 106, "chunks": 4272, "embedded": 4272,
                             "facts": {"facts": 7350, "passed": 4272},
                             "embed_model": "BAAI/bge-m3", "version": "102841c",
                             "checked_at": "2026-10-22T18:00:00+00:00"})
        elif адрес.path == "/api/stats":
            self._json(200, {"documents": 106, "chunks": 4272,
                             "year_from": 1999, "year_to": 2026})
        elif адрес.path == "/api/datasets":
            self._json(200, {"datasets": [{"name": "Билляхская зона", "facts": 12,
                                           "documents": 2}],
                             "total": 1, "pass": {}})
        elif адрес.path == "/api/dataset":
            if ключи.get("name") != "Билляхская зона":
                self._json(400, {"error": "такого в фактах из статей нет",
                                 "known": ["Билляхская зона", "Анабарский щит"]})
                return
            датасет = json.loads(json.dumps(ДАТАСЕТ))
            if not type(self).с_правками:          # до правок страниц и DOI нет
                for связь in датасет["facts"]:
                    for цитата in связь["quotes"]:
                        цитата.pop("pages"), цитата.pop("doi")
            self._json(200, датасет)
        elif адрес.path == "/api/search":
            статья = ключи.get("doc_id")
            if статья and type(self).с_правками:
                попадания = [ФРАГМЕНТ] if статья == ФРАГМЕНТ["doc_id"] else []
            else:                                   # до правок параметр не принят
                попадания = [ФРАГМЕНТ, ЧУЖОЙ]
            self._json(200, {"query": ключи.get("q", ""), "hits": попадания})
        elif адрес.path == "/api/figure":
            данные = b"\xff\xd8\xff\xe0 not a real jpeg"  # первые байты как у jpeg
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(данные)))
            self.end_headers()
            self.wfile.write(данные)
        else:
            self._json(404, {"error": "нет такой страницы"})


@pytest.fixture
def знания():
    """Клиент к подставной базе знаний с применёнными правками."""
    _БазаЗнаний.с_правками = True
    _БазаЗнаний.запросы = []
    сервер = ThreadingHTTPServer(("127.0.0.1", 0), _БазаЗнаний)
    threading.Thread(target=сервер.serve_forever, daemon=True).start()
    yield к.БазаЗнаний(f"http://127.0.0.1:{сервер.server_address[1]}", таймаут=5)
    сервер.shutdown()


@pytest.fixture
def знания_без_правок(знания):
    """Та же база, но ещё без правок: так она отвечает сегодня."""
    _БазаЗнаний.с_правками = False
    return знания


# ------------------------------------------------------------------ состояние
def test_состояние_полное_когда_есть_health(знания):
    с = знания.состояние()
    assert (с["статей"], с["фрагментов"], с["фактов"]) == (106, 4272, 7350)
    assert с["версия"] == "102841c" and с["модель"] == "BAAI/bge-m3"
    assert с["полное"] and not знания.пометки


def test_без_health_состояние_помечено_неполным(знания_без_правок):
    с = знания_без_правок.состояние()
    assert с["статей"] == 106 and с["фрагментов"] == 4272
    assert not с["полное"] and с["версия"] == ""
    assert any("версия" in п for п in знания_без_правок.пометки)


# -------------------------------------------------------------------- датасет
def test_датасет_приносит_связь_с_цитатой(знания):
    д = знания.датасет("Билляхская зона")
    связь = д["facts"][0]
    assert связь["relation"] == "контролируется" and связь["kind"] == "структура"
    цитата = связь["quotes"][0]
    assert к.страница(цитата) == 27 and цитата["doi"] == "10.1/a"


def test_без_правок_у_цитаты_нет_страницы(знания_без_правок):
    цитата = знания_без_правок.датасет("Билляхская зона")["facts"][0]["quotes"][0]
    assert к.страница(цитата) is None, "страницы нет — и выдумывать её нельзя"


def test_неизвестное_имя_приносит_словарь_известных(знания):
    with pytest.raises(к.НетЯкоря) as сбой:
        знания.датасет("обнажённость и растительный покров")
    assert "Анабарский щит" in сбой.value.известные


def test_словарь_имён(знания):
    assert знания.имена() == ["Билляхская зона"]


# ---------------------------------------------------------------------- поиск
def test_поиск_в_границах_статьи(знания):
    попадания = знания.поиск("золото", статья="10.1_a")
    assert [п["doc_id"] for п in попадания] == ["10.1_a"]
    assert "doc_id=10.1_a" in _БазаЗнаний.запросы[-1]
    assert not знания.пометки


def test_если_фильтр_не_принят_отбор_наш_и_это_видно(знания_без_правок):
    попадания = знания_без_правок.поиск("золото", статья="10.1_a")
    assert [п["doc_id"] for п in попадания] == ["10.1_a"], "чужая статья отброшена"
    assert any("на нашей стороне" in п for п in знания_без_правок.пометки)


def test_ссылка_ведёт_на_страницу_pdf(знания):
    assert к.ссылка_на_страницу(ФРАГМЕНТ) == "https://x/a.pdf#page=14"
    assert к.ссылка_на_страницу({"url": "https://x/a", "pages": [3]}) == "https://x/a"


# -------------------------------------------------------------------- рисунок
def test_рисунок_приходит_байтами(знания):
    assert знания.рисунок(12).startswith(b"\xff\xd8\xff")


# ---------------------------------------------------------------------- связи
def test_молчащая_база_знаний_называет_обе_причины():
    знания = к.БазаЗнаний("http://127.0.0.1:9", таймаут=1)
    with pytest.raises(к.НетСвязи) as сбой:
        знания.состояние()
    текст = str(сбой.value)
    assert "georag.py web" in текст and "ssh -N -L" in текст


def test_нет_адреса_значит_правки_не_применены(знания_без_правок):
    with pytest.raises(к.НетСвязи, match="правки из инструкции"):
        знания_без_правок._json("/api/health")
