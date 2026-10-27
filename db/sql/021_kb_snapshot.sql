-- 021: снимок состояния базы знаний статей при каждом обращении к ней.
-- Запускается от gisadmin после 020_grid_label.sql (через db/migrate.py).
--
-- Зачем. База знаний живёт на другой машине и растёт сама: 2 052 статьи на
-- 23.10.2026, граф фактов прибавлял по тысяче фактов в час. Мы узнавали об
-- этом только повторным запуском моста вслепую. Снимок отвечает на два
-- вопроса:
--
--   * стоит ли перезапускать мост — корпус подрос или он тот же;
--   * по какой разметке получена цитата — через полгода «цитата из базы
--     знаний» без версии и объёма той базы ничего не доказывает.
--
-- Снимок — факт о чужой системе, а не суждение, поэтому статуса и
-- подтверждения у него нет: его пишет агент, человек только читает.

CREATE TABLE IF NOT EXISTS meta.kb_snapshot (
    id          bigserial PRIMARY KEY,
    taken_at    timestamptz NOT NULL DEFAULT now(),
    run_id      bigint REFERENCES runs.run(id),
    script      text,                 -- кто снимал: мост, подбор якорей, обмен
    documents   integer,              -- статей
    chunks      integer,              -- фрагментов
    embedded    integer,              -- фрагментов с векторами
    facts       integer,              -- фактов в графе
    entities    integer,              -- сущностей в графе, если граф читался
    embed_model text,
    version     text,                 -- коммит кода базы знаний
    checked_at  timestamptz,          -- время разметки на той стороне
    complete    boolean NOT NULL,     -- false: /api/health не было, часть полей пуста
    note        text
);

COMMENT ON TABLE meta.kb_snapshot IS
    'Состояние базы знаний статей в момент обращения: объём, версия, модель векторов';
COMMENT ON COLUMN meta.kb_snapshot.complete IS
    'Ложь, если база знаний отвечала без /api/health и версия с моделью неизвестны';

CREATE INDEX IF NOT EXISTS kb_snapshot_taken_idx ON meta.kb_snapshot (taken_at DESC);

GRANT SELECT ON meta.kb_snapshot TO gis_read;
GRANT SELECT, INSERT ON meta.kb_snapshot TO gis_agent, gis_human;
GRANT USAGE, SELECT ON SEQUENCE meta.kb_snapshot_id_seq TO gis_agent, gis_human;
