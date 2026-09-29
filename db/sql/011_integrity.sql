-- 011: вердикт — только по списку разрешённых; версии признаков по отпечатку
-- входа; индексы под запросы панели и агента; целостность мелких справочников.
-- Запускается от gisadmin после 010_model_digest.sql. Повторный запуск безопасен.

-- ============================================================ 1. кто выносит вердикт
-- До этой миграции триггер считал человеком любую роль, кроме gis_agent и
-- gis_read. Это список запрещённых: любая новая роль — например, для моста к
-- базе знаний статей — получала право подтверждать автоматически, никто этого
-- не решал. Теперь наоборот: вердикт выносит только тот, кому это явно
-- выдано членством в gis_confirmer. Суперпользователь (gisadmin) проходит сам.
--
-- Второе изменение: «отклонён» — такой же вердикт, как «подтверждён». Раньше
-- агент мог поставить его без строки в meta.review, то есть без причины, и
-- правило «отклонение с причиной» обходилось в один UPDATE.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gis_confirmer') THEN
        CREATE ROLE gis_confirmer NOLOGIN;
    END IF;
END
$$;
COMMENT ON ROLE gis_confirmer IS
    'Право выносить вердикт («подтверждён», «отклонён»); выдаётся членством, не по умолчанию';

GRANT gis_confirmer TO gis_human;

CREATE OR REPLACE FUNCTION meta.guard_confirmation() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
    human boolean := pg_has_role(current_user, 'gis_confirmer', 'MEMBER');
BEGIN
    IF NEW.status IN ('подтверждён', 'отклонён') AND NOT human THEN
        RAISE EXCEPTION
            'Статус «%» в %.% ставит только человек; роль % на это не уполномочена',
            NEW.status, TG_TABLE_SCHEMA, TG_TABLE_NAME, current_user;
    END IF;

    IF NEW.status = 'подтверждён' THEN
        NEW.confirmed_by := coalesce(NEW.confirmed_by, current_user);
        NEW.confirmed_at := coalesce(NEW.confirmed_at, now());
    ELSE
        NEW.confirmed_by := NULL;
        NEW.confirmed_at := NULL;
    END IF;

    RETURN NEW;
END
$fn$;
COMMENT ON FUNCTION meta.guard_confirmation() IS
    'Агент пишет черновики; подтверждает и отклоняет только член gis_confirmer';

-- ================================================ 2. версии признаков по отпечатку
-- Загрузчик теперь ищет признак по (код, сборка, хеш входного файла). Старые
-- записи хеша не знают — без этой дописки первый же прогон после обновления
-- счёл бы все признаки новыми и перегрузил все значения заново. Хеш берётся из
-- журнала того прогона, который признак завёл: это ровно тот файл, из которого
-- легли значения.
UPDATE data.feature f
   SET definition = f.definition || jsonb_build_object('хеш', ri.checksum)
  FROM runs.run_input ri
 WHERE ri.run_id = f.produced_by_run
   AND ri.ref_text = f.definition->>'файл'
   AND NOT (f.definition ? 'хеш');

-- Текущей может быть только одна версия кода в пределах сборки.
CREATE UNIQUE INDEX IF NOT EXISTS feature_one_current
    ON data.feature (code, (definition->>'сборка'))
    WHERE is_current;

-- ======================================================== 3. индексы под запросы
-- Граф происхождения читается на каждой карточке панели и на каждом паспорте
-- агента, а растёт с каждым прогоном: одна операция и одно ребро на паспорт.
CREATE INDEX IF NOT EXISTS derivation_target_idx    ON meta.derivation (target_kind, target_id);
CREATE INDEX IF NOT EXISTS derivation_source_idx    ON meta.derivation (source_kind, source_id);
CREATE INDEX IF NOT EXISTS derivation_operation_idx ON meta.derivation (operation_id);
CREATE INDEX IF NOT EXISTS operation_run_idx        ON meta.operation (run_id);

-- Строки прогона ищутся по номеру прогона.
CREATE INDEX IF NOT EXISTS run_input_run_idx ON runs.run_input (run_id);
CREATE INDEX IF NOT EXISTS metric_run_idx    ON runs.metric (run_id);
CREATE INDEX IF NOT EXISTS artifact_run_idx  ON runs.artifact (run_id);

-- ============================================ 4. связь признак-понятие без дублей
-- Агент перед вставкой проверяет, нет ли уже такой связи, но проверка и вставка
-- — два разных запроса: два прогона рядом заведут дубль. Уникальность держит
-- база; заодно этот индекс обслуживает поиск связей признака в карточке.
DO $$
DECLARE
    дублей integer;
BEGIN
    SELECT count(*) INTO дублей FROM (
        SELECT 1 FROM bridge.feature_concept
         GROUP BY feature_id, concept_code HAVING count(*) > 1) d;
    IF дублей > 0 THEN
        RAISE EXCEPTION
            'В bridge.feature_concept % пар (признак, понятие) заведены дважды — '
            'разберите их в панели до уникального ограничения', дублей;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'feature_concept_one') THEN
        ALTER TABLE bridge.feature_concept
            ADD CONSTRAINT feature_concept_one UNIQUE (feature_id, concept_code);
    END IF;
END
$$;

-- ============================================== 5. закрытые списки вместо текста
-- Опечатка в виде узла графа происхождения молча выводит ребро из всех
-- запросов: 'слои' вместо 'слой' — и признак остаётся без источника.
-- 'публикация' заведена заранее: под неё ляжет обоснование из базы знаний статей.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'derivation_kinds') THEN
        ALTER TABLE meta.derivation ADD CONSTRAINT derivation_kinds CHECK (
            source_kind IN ('файл', 'слой', 'признак', 'паспорт', 'публикация')
            AND target_kind IN ('файл', 'слой', 'признак', 'паспорт', 'публикация'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'run_status') THEN
        ALTER TABLE runs.run ADD CONSTRAINT run_status
            CHECK (status IN ('идёт', 'успех', 'сбой', 'оборван'));
    END IF;
END
$$;
