-- 016: словарь признаков внутри базы — чтобы база стояла без репозитория прогноза.
-- Запускается от gisadmin после 015_feature_check.sql (через db/migrate.py).
--
-- Что было. Техническое описание признака («что это за столбец») и эталонная
-- предметная формулировка («на что он может указывать геологически») жили
-- только в проекте прогноза, в `src/feature_dict.py`, и ядро базы импортировало
-- их напрямую через `GIS_PROJECT_ROOT`. Из этого следовало неприятное: база,
-- отданная другому, не открывала даже карточку признака — панель падала на
-- импорте модуля, которого у него нет. Продаём систему, а она требует рядом
-- чужой исследовательский репозиторий.
--
-- Почему не скопировать модуль сюда. Получились бы две расходящиеся версии
-- одного знания, и через месяц никто не ответит, какая верна.
--
-- Что сделано. Словарь остаётся единственным — в проекте. Но его вывод для
-- кодов, которые есть в базе, загружается сюда снимком: вместе со ссылкой на
-- файл-источник, его отпечатком и номером прогона. База становится
-- самодостаточной, а вопрос «не устарел ли снимок» решается сверкой отпечатка,
-- а не доверием.
--
-- Описание не требует подтверждения человеком: это не суждение о признаке, а
-- перенос уже написанного — ровно как машинные проверки в `meta.feature_check`.

CREATE TABLE IF NOT EXISTS meta.feature_term (
    code        text PRIMARY KEY,
    group_code  text,                  -- группа по словарю: 'ast', 'pf', 'geo2'…
    technical   text NOT NULL,          -- что это за столбец: источник, канал, свёртка
    geo_note    text,                   -- эталонная предметная формулировка, если есть
    source_ref  text,                   -- 'проект:/src/feature_dict.py'
    source_hash text,                   -- отпечаток словаря на момент загрузки
    run_id      bigint REFERENCES runs.run(id),
    loaded_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE meta.feature_term IS
    'Снимок словаря признаков проекта: база не должна требовать его репозиторий рядом';
COMMENT ON COLUMN meta.feature_term.source_hash IS
    'Отпечаток файла-словаря: по нему видно, что снимок отстал от проекта';
COMMENT ON COLUMN meta.feature_term.geo_note IS
    'Формулировка эксперта из проекта — эталон для сверки с выводом модели, не паспорт';

CREATE INDEX IF NOT EXISTS feature_term_group_idx ON meta.feature_term (group_code);

GRANT SELECT ON meta.feature_term TO gis_read;
GRANT SELECT, INSERT, UPDATE ON meta.feature_term TO gis_agent, gis_human;

-- Какие коды базы словарём не покрыты: видно сразу, без выгрузки наружу.
CREATE OR REPLACE VIEW meta.feature_term_gap AS
    SELECT f.code, count(*) AS сборок
      FROM data.feature f
      LEFT JOIN meta.feature_term т ON т.code = f.code
     WHERE т.code IS NULL
     GROUP BY f.code
     ORDER BY f.code;

GRANT SELECT ON meta.feature_term_gap TO gis_read, gis_agent, gis_human;
