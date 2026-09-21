-- 012: литература из базы знаний статей — публикации и цитаты под понятия.
-- Запускается от gisadmin после 011_integrity.sql. Повторный запуск безопасен.
--
-- Схема kb с самого начала задумана под «публикации, понятия, утверждения»,
-- но публикаций в ней не было: в каждом паспорте признака обоснование кончалось
-- словами «публикация не привязана, база знаний в разработке». Базу знаний
-- статей (отдельная машина, отдельный репозиторий georag) спрашивает скрипт
-- scripts/db_kb_evidence.py; сюда ложится то, что она ответила.
--
-- Цитата — черновик, как и всё, что приносит агент: база знаний находит
-- фрагмент статьи и сверяет цитату с текстом, но решает, годится ли она в
-- обоснование, человек.

CREATE TABLE IF NOT EXISTS kb.publication (
    id         bigserial PRIMARY KEY,
    kb_doc_id  text NOT NULL UNIQUE,   -- идентификатор статьи в базе знаний
    doi        text UNIQUE,
    title      text,
    year       integer,
    url        text,
    authors    text[],
    journal    text,
    added_at   timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE kb.publication IS
    'Статьи, на которые ссылаются цитаты; приходят из базы знаний статей';

CREATE TABLE IF NOT EXISTS kb.citation (
    id             bigserial PRIMARY KEY,
    publication_id bigint NOT NULL REFERENCES kb.publication(id),
    concept_code   text   NOT NULL REFERENCES kb.concept(code),
    quote          text   NOT NULL,          -- дословно из статьи
    quote_hash     text   GENERATED ALWAYS AS (md5(quote)) STORED,
    page           integer,
    territory      text,                     -- по какой территории отбирали, если отбирали
    method         text,                     -- по какому методу отбирали, если отбирали
    kb_similarity  real,                     -- похожесть фрагмента на определение понятия
    kb_found_by    text,                     -- вектор | словарь | оба
    kb_verdict     text,                     -- проверила ли фрагмент модель базы знаний
    kb_as_of       timestamptz,              -- по какой разметке базы знаний получено
    run_id         bigint REFERENCES runs.run(id),
    status         meta.status NOT NULL DEFAULT 'черновик',
    confirmed_by   text,
    confirmed_at   timestamptz,
    added_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT citation_once UNIQUE (publication_id, concept_code, quote_hash)
);
COMMENT ON TABLE kb.citation IS
    'Цитата из статьи в обоснование понятия. Черновик, пока человек не подтвердил';
COMMENT ON COLUMN kb.citation.kb_verdict IS
    'Решение модели базы знаний о фрагменте — не решение человека: статус ставит человек';

CREATE INDEX IF NOT EXISTS citation_concept_idx ON kb.citation (concept_code);
CREATE INDEX IF NOT EXISTS citation_run_idx ON kb.citation (run_id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'guard_confirmation'
                   AND tgrelid = 'kb.citation'::regclass) THEN
        CREATE TRIGGER guard_confirmation BEFORE INSERT OR UPDATE ON kb.citation
            FOR EACH ROW EXECUTE FUNCTION meta.guard_confirmation();
    END IF;
END
$$;

GRANT SELECT ON kb.publication, kb.citation TO gis_read;
GRANT SELECT, INSERT, UPDATE ON kb.publication, kb.citation TO gis_agent, gis_human;
GRANT USAGE, SELECT ON SEQUENCE kb.publication_id_seq, kb.citation_id_seq TO gis_agent, gis_human;
