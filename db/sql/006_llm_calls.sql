-- Журнал обращений к языковой модели: что спросили, чем ответили, сколько стоило.
-- Запускается от gisadmin в базе gis_au_u после 003_confirmation_rules.sql.
--
-- Зачем отдельной таблицей, а не полем в runs.run: прогон агента — это сотня
-- обращений, и сравнивать модели между собой (облачную с локальной) нужно
-- поштучно: время ответа, число попыток до разбираемого ответа, сам текст.
-- Без такого журнала переезд на локальную модель нечем обосновать.

CREATE TABLE runs.llm_call (
    id            bigserial PRIMARY KEY,
    run_id        bigint REFERENCES runs.run(id),
    at            timestamptz NOT NULL DEFAULT now(),
    provider      text NOT NULL,     -- 'облако' | 'локальный'
    model         text NOT NULL,     -- 'claude-opus-5', 'qwen2.5-14b-instruct-q4' и т. п.
    task          text,              -- 'смысл признака', 'паспорт слоя'
    subject       text,              -- к чему относится: код признака или слоя
    context_chars integer,           -- длина задачи в символах
    in_tokens     integer,
    out_tokens    integer,
    latency_ms    integer,
    attempts      smallint NOT NULL DEFAULT 1,  -- попыток до разбираемого ответа
    ok            boolean NOT NULL DEFAULT true,
    error         text,
    answer        jsonb              -- разобранный ответ целиком
);

CREATE INDEX llm_call_run_idx     ON runs.llm_call (run_id);
CREATE INDEX llm_call_subject_idx ON runs.llm_call (task, subject);

COMMENT ON TABLE runs.llm_call IS
    'Обращения к языковой модели поштучно: основа для сравнения облачной модели с локальной';
COMMENT ON COLUMN runs.llm_call.attempts IS
    'Слабая модель чаще ошибается форматом; число попыток — прямая мера её пригодности';
