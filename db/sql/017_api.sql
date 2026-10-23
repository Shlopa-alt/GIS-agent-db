-- 017: схема `api` — то, что база показывает наружу.
-- Запускается от gisadmin после 016_feature_term.sql (через db/migrate.py).
--
-- Зачем отдельная схема, а не прямой доступ к таблицам. Внутренние таблицы
-- меняются каждую неделю: в 015 появились проверки признаков, в 016 — словарь.
-- Если читающий снаружи — выгрузка, отчёт, чужая программа — пишет запросы к
-- `meta.feature_passport` напрямую, то любое наше изменение ломает его код, и
-- мы перестаём менять схему из страха. Здесь ровно наоборот: наружу смотрит
-- десяток представлений с устойчивыми именами, а внутри можно двигать что
-- угодно, пока представления выдают то же.
--
-- Два правила этой схемы, оба неслучайные:
--
-- 1. **Только подтверждённое человеком.** Черновик — это незаконченная работа
--    агента; показывать её наружу как знание базы нельзя. Поэтому все
--    представления отбирают `status = 'подтверждён'`. Исключения — машинные
--    проверки и словарь: они подтверждения не требуют по смыслу (факт о
--    расчёте, а не суждение), и это сказано в 015 и 016.
--
-- 2. **Распространяемость видна сразу.** 34 слоя из 40 распространять нельзя:
--    32 — материалы организации, остальные по условиям поставщиков. Это
--    касается не только слоёв, но и значений признаков, из них посчитанных.
--    Представление `api.redistribution` отвечает по каждому признаку, можно ли
--    отдавать его значения, и называет причину. Без такого ответа любая
--    выгрузка — это вопрос «а это точно можно?», заданный человеку, который
--    его не проверит.
--
-- Имена представлений латиницей, столбцы по-русски: имя попадает в чужой код,
-- и кириллица в нём живёт только в кавычках.

CREATE SCHEMA IF NOT EXISTS api;
COMMENT ON SCHEMA api IS
    'Устойчивый вид наружу: только подтверждённое человеком, с правами распространения';

GRANT USAGE ON SCHEMA api TO gis_read, gis_agent, gis_human;

-- ------------------------------------------------------------------ признаки
CREATE OR REPLACE VIEW api.feature AS
    SELECT f.code                       AS признак,
           coalesce(f.group_code, т.group_code) AS группа,
           f.definition->>'сборка'      AS сборка,
           g.code                       AS сетка,
           terr.title                   AS территория,
           т.technical                  AS техническое_описание,
           p.geological_meaning         AS предметный_смысл,
           p.kb_reference               AS литература,
           p.aggregation_rule           AS свёртка_в_ячейку,
           p.coverage_frac              AS покрытие,
           p.missing_frac               AS пропусков,
           p.footprint_dependent        AS зависит_от_контура_съёмки,
           p.circularity                AS вход_критериальной_формулы,
           p.transferability            AS переносимость,
           пр.verdict                   AS важность,
           пр.value                     AS вклад_в_прогноз,
           p.confirmed_by               AS подтвердил,
           p.confirmed_at               AS подтверждено
      FROM meta.feature_passport p
      JOIN data.feature f ON f.id = p.feature_id
      LEFT JOIN meta.feature_term т ON т.code = f.code
      LEFT JOIN data.grid g ON g.code = f.definition->>'сетка'
      LEFT JOIN data.territory terr ON terr.id = g.territory_id
      LEFT JOIN meta.feature_check пр
             ON пр.feature_id = p.feature_id AND пр.kind = 'важность'
     WHERE p.status = 'подтверждён';

COMMENT ON VIEW api.feature IS
    'Паспорта признаков, подтверждённые человеком: смысл, покрытие, проверки';

-- --------------------------------------------------------------------- слои
CREATE OR REPLACE VIEW api.layer AS
    SELECT l.code                       AS слой,
           l.title                      AS название,
           l.kind                       AS вид,
           l.provider                   AS поставщик,
           l.resolution_m               AS разрешение_м,
           l.format                     AS формат,
           l.published_on               AS опубликован,
           lp.source_note               AS откуда,
           lp.method                    AS как_получен,
           lp.coverage_frac             AS покрытие,
           lp.footprint_note            AS о_контуре_съёмки,
           lic.code                     AS лицензия,
           lic.title                    AS лицензия_название,
           lic.url                       AS лицензия_адрес,
           lic.allows_redistribution    AS можно_распространять,
           lp.confirmed_by              AS подтвердил
      FROM meta.layer_passport lp
      JOIN data.source_layer l ON l.id = lp.layer_id
      LEFT JOIN meta.license lic ON lic.id = lp.license_id
     WHERE lp.status = 'подтверждён';

COMMENT ON VIEW api.layer IS
    'Паспорта слоёв-источников с лицензией: что за данные и можно ли их отдавать';

-- ----------------------------------------------------------------- понятия
CREATE OR REPLACE VIEW api.concept AS
    SELECT c.code AS понятие, c.kind AS вид, c.definition AS определение,
           c.source AS источник, c.confirmed_by AS подтвердил
      FROM kb.concept c
     WHERE c.status = 'подтверждён';

CREATE OR REPLACE VIEW api.feature_concept AS
    SELECT f.code AS признак, fc.concept_code AS понятие, fc.role AS роль,
           fc.origin AS откуда_связь, fc.evidence_ref AS обоснование,
           fc.confirmed_by AS подтвердил
      FROM bridge.feature_concept fc
      JOIN data.feature f ON f.id = fc.feature_id
     WHERE fc.status = 'подтверждён';

COMMENT ON VIEW api.feature_concept IS
    'Связи «признак — понятие предметной модели», подтверждённые человеком';

-- ------------------------------------------------------------------ цитаты
CREATE OR REPLACE VIEW api.citation AS
    SELECT ц.concept_code AS понятие, ц.quote AS цитата, ц.page AS страницы,
           ц.territory AS территория, ц.method AS метод,
           п.title AS статья, п.authors AS авторы, п.year AS год,
           п.journal AS издание, п.doi AS doi, п.url AS адрес,
           ц.confirmed_by AS подтвердил
      FROM kb.citation ц
      JOIN kb.publication п ON п.id = ц.publication_id
     WHERE ц.status = 'подтверждён';

COMMENT ON VIEW api.citation IS
    'Цитаты из опубликованных статей под понятиями — наши ссылки, не чужой текст целиком';

-- ------------------------------------------------------------- территории
CREATE OR REPLACE VIEW api.territory AS
    SELECT t.code AS территория, t.title AS название, t.scale AS масштаб,
           t.is_primary AS основная,
           (SELECT count(*) FROM data.grid g WHERE g.territory_id = t.id) AS сеток
      FROM data.territory t;

-- --------------------------------------------------- права распространения
-- Главное представление для выгрузки. Признак нельзя отдавать, если хотя бы
-- один слой, из которого он посчитан, распространять нельзя: значения несут в
-- себе содержимое слоя, пусть и свёрнутое в ячейку.
CREATE OR REPLACE VIEW api.redistribution AS
    SELECT f.code AS признак,
           bool_and(coalesce(lic.allows_redistribution, false))
               FILTER (WHERE lic.id IS NOT NULL)      AS можно_распространять,
           count(DISTINCT l.id)                        AS слоёв_источников,
           string_agg(DISTINCT lic.code, ', ')         AS лицензии,
           string_agg(DISTINCT lic.code, ', ') FILTER (
               WHERE NOT lic.allows_redistribution)    AS что_запрещает
      FROM data.feature f
      LEFT JOIN meta.derivation d
             ON d.target_kind = 'признак' AND d.target_id = f.id
            AND d.source_kind = 'слой'
      LEFT JOIN data.source_layer l ON l.id = d.source_id
      LEFT JOIN meta.layer_passport lp ON lp.layer_id = l.id
      LEFT JOIN meta.license lic ON lic.id = lp.license_id
     GROUP BY f.code;

COMMENT ON VIEW api.redistribution IS
    'Можно ли отдавать значения признака: запрещает любой нераспространяемый слой-источник';
COMMENT ON COLUMN api.redistribution.можно_распространять IS
    'NULL — слоёв-источников нет вовсе: признак посчитан внутри проекта, решает человек';

-- ------------------------------------------------------------------ состав
CREATE OR REPLACE VIEW api.summary AS
    SELECT 'признаков с подтверждённым паспортом' AS что, count(*)::bigint AS сколько
      FROM api.feature
    UNION ALL SELECT 'из них можно распространять значения',
           count(*) FROM api.redistribution WHERE можно_распространять
    UNION ALL SELECT 'слоёв-источников', count(*) FROM api.layer
    UNION ALL SELECT 'из них можно распространять',
           count(*) FROM api.layer WHERE можно_распространять
    UNION ALL SELECT 'понятий', count(*) FROM api.concept
    UNION ALL SELECT 'связей признак-понятие', count(*) FROM api.feature_concept
    UNION ALL SELECT 'цитат', count(*) FROM api.citation
    UNION ALL SELECT 'территорий', count(*) FROM api.territory
    UNION ALL SELECT 'ячеек', count(*) FROM data.cell
    UNION ALL SELECT 'значений признаков', count(*) FROM data.feature_value;

COMMENT ON VIEW api.summary IS 'Состав базы одним запросом — для отчёта и для проверки выгрузки';

GRANT SELECT ON ALL TABLES IN SCHEMA api TO gis_read, gis_agent, gis_human;
