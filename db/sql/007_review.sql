-- Панель подтверждений: роль человека, журнал решений, очередь на проверку.
-- Запускается от gisadmin в базе gis_au_u после 006_llm_calls.sql.
--
-- Правило проекта «черновик пишет агент, подтверждает человек» до сих пор было
-- выражено только запретом: триггер meta.guard_confirmation не пускает роль
-- gis_agent. Обратная сторона правила — место, где человек решение принимает и
-- где оно остаётся видимым: кто смотрел, что решил, почему отклонил и что
-- поправил. Без этого «подтверждено» означает лишь «кто-то нажал кнопку».

-- ------------------------------------------------- роль человека за панелью
-- Панель не должна ходить в базу суперпользователем: ей нужно ровно то же, что
-- агенту, плюс право ставить статус «подтверждён». Для триггера человек — это
-- любая роль, кроме gis_agent и gis_read.
-- Пароль задаётся отдельно: python db/set_password.py gis_human
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gis_human') THEN
        CREATE ROLE gis_human LOGIN;
    END IF;
END
$$;
COMMENT ON ROLE gis_human IS
    'Человек за панелью подтверждений: пишет как агент, но вправе подтверждать';

GRANT USAGE ON SCHEMA data, meta, runs, kb, bridge, staging TO gis_human;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA data, meta, runs, kb, bridge, staging TO gis_human;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA data, meta, runs, kb, bridge, staging TO gis_human;
ALTER DEFAULT PRIVILEGES IN SCHEMA data, meta, runs, kb, bridge, staging
    GRANT SELECT, INSERT, UPDATE ON TABLES TO gis_human;
ALTER DEFAULT PRIVILEGES IN SCHEMA data, meta, runs, kb, bridge, staging
    GRANT USAGE, SELECT ON SEQUENCES TO gis_human;

-- ----------------------------------------------------- журнал решений человека
CREATE TABLE meta.review (
    id          bigserial PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    reviewer    text NOT NULL,          -- кто смотрел (подпись, а не логин роли)
    object_kind text NOT NULL CHECK (object_kind IN (
        'паспорт признака', 'паспорт слоя', 'лицензия', 'связь признак-понятие')),
    object_id   bigint NOT NULL,
    object_ref  text,                   -- код признака или слоя: читается без соединений
    decision    meta.status NOT NULL,   -- 'подтверждён' | 'отклонён' | 'черновик' (правка без решения)
    comment     text,
    edited      jsonb,                  -- поле -> {было, стало}: что человек исправил
    model       text,                   -- чей черновик оценивали
    seconds     integer,                -- сколько карточка была открыта
    CONSTRAINT отклонение_с_причиной
        CHECK (decision <> 'отклонён' OR coalesce(comment, '') <> '')
);

CREATE INDEX review_object_idx ON meta.review (object_kind, object_id);
CREATE INDEX review_at_idx     ON meta.review (at DESC);

COMMENT ON TABLE meta.review IS
    'Решения человека по черновикам: подтверждение, отклонение с причиной, правка';
COMMENT ON COLUMN meta.review.edited IS
    'Правка человека — самая дорогая разметка в проекте: показывает, чего модели не хватило';
COMMENT ON CONSTRAINT отклонение_с_причиной ON meta.review IS
    'Отклонение без причины ничему не учит ни агента, ни следующего проверяющего';

-- ------------------------------------------------------ очередь на проверку
-- Один взгляд на всё, что ждёт человека, — и для панели, и для отчёта о ходе
-- паспортизации.
CREATE VIEW meta.confirmation_queue AS
    SELECT 'паспорт признака' AS вид, p.feature_id AS id, f.code AS код,
           p.status, p.model, p.updated_at AS изменён
      FROM meta.feature_passport p
      JOIN data.feature f ON f.id = p.feature_id
    UNION ALL
    SELECT 'паспорт слоя', p.layer_id, l.code, p.status, p.model, p.updated_at
      FROM meta.layer_passport p
      JOIN data.source_layer l ON l.id = p.layer_id
    UNION ALL
    SELECT 'лицензия', l.id, l.code, l.status, NULL, NULL
      FROM meta.license l
    UNION ALL
    SELECT 'связь признак-понятие', b.id, f.code || ' → ' || b.concept_code,
           b.status, r.model, NULL
      FROM bridge.feature_concept b
      JOIN data.feature f ON f.id = b.feature_id
      LEFT JOIN runs.run r ON r.id = b.run_id;

COMMENT ON VIEW meta.confirmation_queue IS
    'Всё, что ждёт решения человека, одним списком: вид, объект, статус, чей черновик';

-- ---------------------------------------------- как модели проходят проверку
-- Доля принятых черновиков по моделям — прямая мера пригодности исполнителя.
-- Ради неё и затевался журнал: сравнивать облачную модель с локальной по
-- решениям человека, а не по впечатлению от текста.
CREATE VIEW meta.model_score AS
    SELECT coalesce(model, 'без модели') AS модель,
           count(*)                                                    AS решений,
           count(*) FILTER (WHERE decision = 'подтверждён')             AS подтверждено,
           count(*) FILTER (WHERE decision = 'отклонён')                AS отклонено,
           count(*) FILTER (WHERE edited IS NOT NULL AND edited <> '{}'::jsonb) AS "с правкой",
           round((count(*) FILTER (WHERE decision = 'подтверждён'))::numeric
                 / nullif(count(*) FILTER (WHERE decision <> 'черновик'), 0), 3) AS "доля принятых"
      FROM meta.review
     GROUP BY 1;

COMMENT ON VIEW meta.model_score IS
    'Доля черновиков модели, принятых человеком, — основание для выбора исполнителя';

GRANT SELECT ON meta.confirmation_queue, meta.model_score TO gis_read, gis_agent, gis_human;
GRANT SELECT, INSERT ON meta.review TO gis_human;
GRANT SELECT ON meta.review TO gis_read, gis_agent;
GRANT USAGE, SELECT ON SEQUENCE meta.review_id_seq TO gis_human;
