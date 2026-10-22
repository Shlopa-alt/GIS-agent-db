-- 013: чем связаны две базы — соответствие «понятие → сущность в графе знаний»;
-- цитаты и соответствия встают в очередь к человеку.
-- Запускается от gisadmin после 012_kb_citations.sql. Повторный запуск безопасен.
--
-- Почему понадобилось. До 14.10.2026 у базы знаний был словарь понятий, и общий
-- ключ двух баз был прямой: код понятия здесь и код там совпадали буква в букву
-- (на этом построен scripts/db_kb_evidence.py). Словаря там больше нет: знание
-- лежит типизированным графом фактов — 9 видов концов, 14 связей, за каждым
-- фактом дословная цитата. Сопоставлять понятие теперь нужно явно, и это
-- решение человека, а не совпадение строк.
--
-- Что здесь появляется:
--   1. bridge.concept_anchor — чему в графе отвечает понятие;
--   2. bridge.feature_concept.citation_id — связь признака с понятием получает
--      право опереться на цитату из статьи;
--   3. цитаты и соответствия — в очередь подтверждений панели (без этого они
--      остались бы черновиками навсегда: человек их просто не видит).

-- ============================================== 1. понятие → сущность в графе
CREATE TABLE IF NOT EXISTS bridge.concept_anchor (
    id           bigserial PRIMARY KEY,
    concept_code text NOT NULL REFERENCES kb.concept(code),
    kb_name      text,                      -- имя сущности в графе базы знаний
    relations    text[] NOT NULL DEFAULT '{}',  -- какие связи графа брать в работу
    query        text,                      -- запасной запрос по фрагментам статей
    absent       boolean NOT NULL DEFAULT false, -- в графе соответствия нет
    status       meta.status NOT NULL DEFAULT 'черновик',
    confirmed_by text,
    confirmed_at timestamptz,
    note         text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    -- Либо якорь назван, либо явно записано, что его нет. Пустая строка без
    -- решения — худшее из состояний: выглядит как работа, а работы нет.
    CONSTRAINT anchor_named_or_absent CHECK (absent OR kb_name IS NOT NULL),
    CONSTRAINT anchor_once UNIQUE (concept_code, kb_name)
);

COMMENT ON TABLE bridge.concept_anchor IS
    'Чему понятие отвечает в графе базы знаний; у понятия может быть несколько якорей';
COMMENT ON COLUMN bridge.concept_anchor.absent IS
    'Соответствия в графе нет — например, условие наблюдения не описывают статьи по геологии';
COMMENT ON COLUMN bridge.concept_anchor.relations IS
    'Связи графа, осмысленные для этого понятия: «контролируется», «указывает на», «выявлено методом»…';
COMMENT ON COLUMN bridge.concept_anchor.query IS
    'Запрос по фрагментам статей, когда факта в графе нет, а в тексте статьи сказано';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'guard_confirmation'
                   AND tgrelid = 'bridge.concept_anchor'::regclass) THEN
        CREATE TRIGGER guard_confirmation BEFORE INSERT OR UPDATE ON bridge.concept_anchor
            FOR EACH ROW EXECUTE FUNCTION meta.guard_confirmation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'touch_updated_at'
                   AND tgrelid = 'bridge.concept_anchor'::regclass) THEN
        CREATE TRIGGER touch_updated_at BEFORE UPDATE ON bridge.concept_anchor
            FOR EACH ROW EXECUTE FUNCTION meta.touch_updated_at();
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS concept_anchor_concept_idx
    ON bridge.concept_anchor (concept_code);

GRANT SELECT ON bridge.concept_anchor TO gis_read;
GRANT SELECT, INSERT, UPDATE ON bridge.concept_anchor TO gis_agent, gis_human;
GRANT USAGE, SELECT ON SEQUENCE bridge.concept_anchor_id_seq TO gis_agent, gis_human;

-- ===================================== 2. связь признак-понятие ← цитата
-- Текст обоснования, который человек уже подтвердил, мост не переписывает:
-- литература приходит отдельной ссылкой на цитату. Так подтверждённое решение
-- остаётся тем, что подтвердили, а публикация добавляется рядом.
ALTER TABLE bridge.feature_concept
    ADD COLUMN IF NOT EXISTS citation_id bigint REFERENCES kb.citation(id);

COMMENT ON COLUMN bridge.feature_concept.citation_id IS
    'Цитата из статьи под эту связь; origin при ней — «внешняя публикация»';

CREATE INDEX IF NOT EXISTS feature_concept_citation_idx
    ON bridge.feature_concept (citation_id) WHERE citation_id IS NOT NULL;

-- =============================================== 3. очередь к человеку
-- Журнал решений должен принимать новые виды объектов.
ALTER TABLE meta.review DROP CONSTRAINT IF EXISTS review_object_kind_check;
ALTER TABLE meta.review ADD CONSTRAINT review_object_kind_check
    CHECK (object_kind IN ('паспорт признака', 'паспорт слоя', 'лицензия',
                           'связь признак-понятие', 'понятие',
                           'цитата', 'якорь понятия'));

CREATE OR REPLACE VIEW meta.confirmation_queue AS
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
      LEFT JOIN runs.run r ON r.id = b.run_id
    UNION ALL
    SELECT 'понятие', c.id, c.code, c.status, NULL, c.updated_at
      FROM kb.concept c
    UNION ALL
    -- Цитата: в коде видно, к какому понятию она пришла и из какой статьи, —
    -- человек в очереди должен понимать, что открывает, до открытия.
    SELECT 'цитата', ц.id, ц.concept_code || ' ← ' ||
           coalesce(left(п.title, 60), п.kb_doc_id) ||
           coalesce(', с. ' || ц.page::text, ''),
           ц.status, r.model, ц.added_at
      FROM kb.citation ц
      JOIN kb.publication п ON п.id = ц.publication_id
      LEFT JOIN runs.run r ON r.id = ц.run_id
    UNION ALL
    SELECT 'якорь понятия', я.id,
           я.concept_code || ' → ' || coalesce(я.kb_name, 'в графе нет'),
           я.status, NULL, я.updated_at
      FROM bridge.concept_anchor я;

GRANT SELECT ON meta.confirmation_queue TO gis_read, gis_agent, gis_human;
