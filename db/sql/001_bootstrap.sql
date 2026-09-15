-- Базовая разметка базы данных проекта (узлы 3-4 технологической схемы).
-- Запускается один раз от имени gisadmin в базе gis_au_u.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_raster;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Схемы: данные, паспорта и происхождение, журнал прогонов,
-- база знаний, мост между данными и знаниями, приёмный буфер агента.
CREATE SCHEMA IF NOT EXISTS data;
CREATE SCHEMA IF NOT EXISTS meta;
CREATE SCHEMA IF NOT EXISTS runs;
CREATE SCHEMA IF NOT EXISTS kb;
CREATE SCHEMA IF NOT EXISTS bridge;
CREATE SCHEMA IF NOT EXISTS staging;

COMMENT ON SCHEMA data   IS 'Территории, слои-источники, признаки, ячейки, значения';
COMMENT ON SCHEMA meta   IS 'Паспорта слоёв и признаков, происхождение, реестр пробелов';
COMMENT ON SCHEMA runs   IS 'Журнал прогонов: версия кода, манифест входов, метрики';
COMMENT ON SCHEMA kb     IS 'База знаний: публикации, понятия, утверждения (в разработке)';
COMMENT ON SCHEMA bridge IS 'Связи данных и знаний; правки требуют подтверждения человеком';
COMMENT ON SCHEMA staging IS 'Приёмный буфер: скачанное агентом до паспортизации';
