-- Права ролей и правило «подтверждает человек».
-- Три исключения из полной автоматики: удаление, лицензия, геологический смысл.
-- Запускается от gisadmin в базе gis_au_u после 002_schema.sql.

-- ------------------------------------------------------------------- права
GRANT USAGE ON SCHEMA data, meta, runs, kb, bridge, staging TO gis_agent, gis_read;

GRANT SELECT ON ALL TABLES IN SCHEMA data, meta, runs, kb, bridge, staging TO gis_read;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA data, meta, runs, kb, bridge, staging TO gis_agent;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA data, meta, runs, kb, bridge, staging TO gis_agent;

ALTER DEFAULT PRIVILEGES IN SCHEMA data, meta, runs, kb, bridge, staging
    GRANT SELECT ON TABLES TO gis_read;
ALTER DEFAULT PRIVILEGES IN SCHEMA data, meta, runs, kb, bridge, staging
    GRANT SELECT, INSERT, UPDATE ON TABLES TO gis_agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA data, meta, runs, kb, bridge, staging
    GRANT USAGE, SELECT ON SEQUENCES TO gis_agent;

-- В staging агент полноправен: это его черновая площадка, там удалять можно.
GRANT ALL ON SCHEMA staging TO gis_agent;
GRANT DELETE, TRUNCATE ON ALL TABLES IN SCHEMA staging TO gis_agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA staging GRANT ALL ON TABLES TO gis_agent;

-- --------------------------------------------------- подтверждение человеком
CREATE OR REPLACE FUNCTION meta.guard_confirmation() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
    human boolean := current_user NOT IN ('gis_agent', 'gis_read');
BEGIN
    IF NEW.status = 'подтверждён' AND NOT human THEN
        RAISE EXCEPTION
            'Статус «подтверждён» в %.% ставит только человек; роль % на это не уполномочена',
            TG_TABLE_SCHEMA, TG_TABLE_NAME, current_user;
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
    'Агент пишет черновики, подтверждает человек: лицензия, паспорт слоя, геологический смысл признака, связь с понятием';

CREATE TRIGGER guard_confirmation BEFORE INSERT OR UPDATE ON meta.license
    FOR EACH ROW EXECUTE FUNCTION meta.guard_confirmation();
CREATE TRIGGER guard_confirmation BEFORE INSERT OR UPDATE ON meta.layer_passport
    FOR EACH ROW EXECUTE FUNCTION meta.guard_confirmation();
CREATE TRIGGER guard_confirmation BEFORE INSERT OR UPDATE ON meta.feature_passport
    FOR EACH ROW EXECUTE FUNCTION meta.guard_confirmation();
CREATE TRIGGER guard_confirmation BEFORE INSERT OR UPDATE ON bridge.feature_concept
    FOR EACH ROW EXECUTE FUNCTION meta.guard_confirmation();

-- -------------------------------------------- выселение файла тоже не молча
CREATE OR REPLACE FUNCTION data.guard_eviction() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.evicted AND coalesce(NEW.evict_note, '') = '' THEN
        RAISE EXCEPTION
            'Слой % помечен выселенным без пометки, как его восстановить', NEW.code;
    END IF;
    RETURN NEW;
END
$fn$;

CREATE TRIGGER guard_eviction BEFORE INSERT OR UPDATE ON data.source_layer
    FOR EACH ROW EXECUTE FUNCTION data.guard_eviction();

-- ----------------------------------------------- отметка времени правки паспорта
CREATE OR REPLACE FUNCTION meta.touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END
$fn$;

CREATE TRIGGER touch_updated_at BEFORE UPDATE ON meta.layer_passport
    FOR EACH ROW EXECUTE FUNCTION meta.touch_updated_at();
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON meta.feature_passport
    FOR EACH ROW EXECUTE FUNCTION meta.touch_updated_at();
