-- 015: машинные проверки признака — важность, переносимость, покрытие.
-- Запускается от gisadmin после 014_migrations.sql (через db/migrate.py).
--
-- Зачем отдельная таблица, а не столбцы паспорта. В `meta.feature_passport`
-- есть `importance_checked` и `transferability`, и они не заполнены: первый —
-- ноль из 240, второй — «не проверен» у 135 из 240. Заполнить их теперь нельзя
-- даже технически: все 240 паспортов подтверждены, а правило базы не даёт роли
-- агента изменить подтверждённую строку вообще — проверяется `NEW.status`, то
-- есть любой UPDATE такой строки запрещён.
--
-- Но дело не только в правиле. «Важность признака посчитана в прогоне N по
-- таблице M» — это факт о расчёте, а не суждение о признаке: он проверяется
-- механически и подтверждения человеком не требует, в отличие от фразы
-- «признак отражает зоны окварцевания». Поэтому факты о проверках живут
-- отдельно и пишутся агентом, а паспорт остаётся тем, что подтвердил человек.
--
-- Столбцы паспорта не удаляются: на них ссылается панель и собранные документы.
-- Они становятся тем, чем были по смыслу, — отметкой человека при подтверждении.

CREATE TABLE IF NOT EXISTS meta.feature_check (
    id           bigserial PRIMARY KEY,
    feature_id   integer NOT NULL REFERENCES data.feature(id),
    kind         text NOT NULL CHECK (kind IN (
                     'важность',        -- участвовал в расчёте важности
                     'переносимость',   -- проверен переносом на смежную территорию
                     'покрытие',        -- доля ячеек со значением пересчитана
                     'циркулярность')), -- вход критериальной формулы
    verdict      text NOT NULL,         -- 'важен', 'непереносим', 'не проверен'…
    value        double precision,      -- число, если проверка его даёт
    source_ref   text,                  -- таблица-источник: 'проект:/outputs/metrics/…'
    source_hash  text,                  -- отпечаток источника на момент проверки
    note         text,
    run_id       bigint REFERENCES runs.run(id),
    checked_at   timestamptz NOT NULL DEFAULT now(),
    checked_by   text NOT NULL DEFAULT current_user,
    -- Одна проверка каждого вида на признак: новая перезаписывает прежнюю, а
    -- история остаётся в журнале прогонов.
    CONSTRAINT feature_check_once UNIQUE (feature_id, kind)
);

COMMENT ON TABLE meta.feature_check IS
    'Машинные проверки признака: факты о расчётах, не требующие подтверждения человеком';
COMMENT ON COLUMN meta.feature_check.source_hash IS
    'Отпечаток таблицы-источника: без него «важность посчитана» недоказуемо';
COMMENT ON COLUMN meta.feature_check.verdict IS
    'Итог словами — его показывает панель рядом с паспортом';

CREATE INDEX IF NOT EXISTS feature_check_feature_idx ON meta.feature_check (feature_id);
CREATE INDEX IF NOT EXISTS feature_check_kind_idx ON meta.feature_check (kind);

GRANT SELECT ON meta.feature_check TO gis_read;
GRANT SELECT, INSERT, UPDATE ON meta.feature_check TO gis_agent, gis_human;
GRANT USAGE, SELECT ON SEQUENCE meta.feature_check_id_seq TO gis_agent, gis_human;

-- Сводка по коду признака: сборок у кода несколько, а проверка одна и та же.
CREATE OR REPLACE VIEW meta.feature_check_summary AS
    SELECT f.code,
           max(п.verdict) FILTER (WHERE п.kind = 'важность')      AS важность,
           max(п.verdict) FILTER (WHERE п.kind = 'переносимость') AS переносимость,
           max(п.value)   FILTER (WHERE п.kind = 'важность')      AS вклад,
           max(п.checked_at)                                      AS проверено
      FROM meta.feature_check п
      JOIN data.feature f ON f.id = п.feature_id
     GROUP BY f.code;

GRANT SELECT ON meta.feature_check_summary TO gis_read, gis_agent, gis_human;

COMMENT ON COLUMN meta.feature_passport.importance_checked IS
    'Отметка человека при подтверждении; машинные проверки — в meta.feature_check';
