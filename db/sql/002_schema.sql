-- Схема данных, паспортов, происхождения и журнала прогонов.
-- Узлы 3-4 технологической схемы. Запускается от gisadmin в базе gis_au_u.

-- ---------------------------------------------------------------- перечисления
CREATE TYPE meta.status AS ENUM ('черновик', 'подтверждён', 'отклонён');
CREATE TYPE meta.transferability AS ENUM ('переносим', 'непереносим', 'не проверен');
CREATE TYPE meta.access AS ENUM ('открыт', 'запрошен', 'отказано', 'закрыт');
CREATE TYPE meta.origin AS ENUM ('внешняя публикация', 'наш результат');

-- ------------------------------------------------------------------ территории
CREATE TABLE data.territory (
    id          serial PRIMARY KEY,
    code        text NOT NULL UNIQUE,             -- 'R-48-XI,XII'
    title       text,
    scale       text,                             -- '1:200 000'
    is_primary  boolean NOT NULL DEFAULT false,   -- основной лист или смежный
    geom        geometry(MultiPolygon, 4326),
    note        text
);
COMMENT ON TABLE data.territory IS 'Листы и участки; территория — измерение базы, не константа';

-- -------------------------------------------------------------- слои-источники
CREATE TABLE data.source_layer (
    id            serial PRIMARY KEY,
    code          text NOT NULL UNIQUE,
    title         text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('вектор', 'растр', 'точки', 'таблица')),
    provider      text,                           -- кто выпустил
    url           text,                           -- откуда качается
    path          text,                           -- где лежит файл (вне базы)
    format        text,
    crs           text,
    resolution_m  double precision,
    acquired_from date,
    acquired_to   date,
    published_on  date,
    footprint     geometry(MultiPolygon, 4326),   -- реальный контур съёмки
    checksum      text,
    size_bytes    bigint,
    evicted       boolean NOT NULL DEFAULT false, -- файл выселен с диска
    evict_note    text,                           -- как восстановить
    added_at      timestamptz NOT NULL DEFAULT now(),
    added_by      text NOT NULL DEFAULT current_user
);
COMMENT ON COLUMN data.source_layer.evicted IS
    'Файл удалён с диска ради места; паспорт, хеш и ссылка сохранены';
CREATE INDEX ON data.source_layer USING gist (footprint);

-- ------------------------------------------------------------- сетка и ячейки
CREATE TABLE data.grid (
    id           serial PRIMARY KEY,
    code         text NOT NULL UNIQUE,
    cell_size_m  integer NOT NULL,
    crs          text NOT NULL,
    territory_id integer REFERENCES data.territory(id)
);

CREATE TABLE data.cell (
    id       bigserial PRIMARY KEY,
    grid_id  integer NOT NULL REFERENCES data.grid(id),
    col      integer NOT NULL,
    row      integer NOT NULL,
    geom     geometry(Polygon, 4326) NOT NULL,
    centroid geometry(Point, 4326),
    UNIQUE (grid_id, col, row)
);
CREATE INDEX ON data.cell USING gist (geom);

-- -------------------------------------------------------------------- признаки
CREATE TABLE data.feature (
    id              serial PRIMARY KEY,
    code            text NOT NULL,                -- 'ast_kaolin'
    version         integer NOT NULL DEFAULT 1,   -- меняется от сырья, кода ИЛИ параметров
    group_code      text,                         -- 'ast', 'pf', 'gm'
    title           text,
    unit            text,
    dtype           text,
    definition      jsonb,                        -- скрипт, параметры, входные слои
    produced_by_run bigint,
    valid_from      timestamptz NOT NULL DEFAULT now(),
    valid_to        timestamptz,
    is_current      boolean NOT NULL DEFAULT true,
    UNIQUE (code, version)
);
CREATE INDEX ON data.feature (code) WHERE is_current;

CREATE TABLE data.feature_value (
    feature_id integer NOT NULL REFERENCES data.feature(id),
    cell_id    bigint  NOT NULL REFERENCES data.cell(id),
    value      double precision,
    PRIMARY KEY (feature_id, cell_id)
) PARTITION BY HASH (feature_id);

DO $partitions$
BEGIN
    FOR i IN 0..15 LOOP
        EXECUTE format(
            'CREATE TABLE data.feature_value_p%s PARTITION OF data.feature_value
             FOR VALUES WITH (MODULUS 16, REMAINDER %s)', i, i);
    END LOOP;
END
$partitions$;

-- -------------------------------------------------------------------- паспорта
CREATE TABLE meta.license (
    id                    serial PRIMARY KEY,
    code                  text NOT NULL UNIQUE,
    title                 text,
    url                   text,
    allows_redistribution boolean,
    status                meta.status NOT NULL DEFAULT 'черновик',
    confirmed_by          text,
    confirmed_at          timestamptz
);

CREATE TABLE meta.layer_passport (
    layer_id       integer PRIMARY KEY REFERENCES data.source_layer(id),
    license_id     integer REFERENCES meta.license(id),
    source_note    text,          -- откуда взят, чем подтверждается
    method         text,          -- как получен и приведён
    coverage_frac  double precision,
    missing_frac   double precision,
    footprint_note text,          -- чем контур съёмки может сместить выборку
    model          text,          -- какой моделью составлен черновик
    status         meta.status NOT NULL DEFAULT 'черновик',
    confirmed_by   text,
    confirmed_at   timestamptz,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE meta.feature_passport (
    feature_id          integer PRIMARY KEY REFERENCES data.feature(id),
    geological_meaning  text,    -- что признак значит геологически
    kb_reference        text,    -- обоснование в базе знаний
    aggregation_rule    text,    -- как слой свёрнут в ячейку
    coverage_frac       double precision,
    missing_frac        double precision,
    footprint_dependent boolean, -- зависит ли от контура съёмки
    circularity         boolean, -- входит ли в критериальную формулу ВНИГНИ
    transferability     meta.transferability NOT NULL DEFAULT 'не проверен',
    importance_checked  boolean NOT NULL DEFAULT false,
    model               text,
    status              meta.status NOT NULL DEFAULT 'черновик',
    confirmed_by        text,
    confirmed_at        timestamptz,
    updated_at          timestamptz NOT NULL DEFAULT now()
);
COMMENT ON COLUMN meta.feature_passport.circularity IS
    'Прямой вход критериальной формулы: как признак независимой модели — утечка';

-- ------------------------------------------------------------ реестр пробелов
CREATE TABLE meta.gap (
    id           serial PRIMARY KEY,
    code         text NOT NULL UNIQUE,
    title        text NOT NULL,
    what_needed  text,
    why_needed   text,
    holder       text,          -- у кого данные лежат
    access       meta.access NOT NULL DEFAULT 'закрыт',
    requested_at date,
    priority     integer,
    note         text
);
COMMENT ON TABLE meta.gap IS
    'Данные, которых нет; питает контур «Данных достаточно? → НЕТ»';

-- --------------------------------------------------- происхождение (уровень 2)
CREATE TABLE meta.operation (
    id         bigserial PRIMARY KEY,
    kind       text NOT NULL,   -- 'скачивание', 'пересчёт', 'агрегация'
    script     text,
    git_commit text,
    params     jsonb,
    run_id     bigint,
    at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE meta.derivation (
    id           bigserial PRIMARY KEY,
    operation_id bigint NOT NULL REFERENCES meta.operation(id),
    source_kind  text NOT NULL,  -- 'слой', 'признак', 'файл'
    source_id    bigint,
    source_ref   text,
    target_kind  text NOT NULL,
    target_id    bigint,
    target_ref   text
);
COMMENT ON TABLE meta.derivation IS
    'Рёбра графа происхождения: что из чего и какой операцией получено';

-- ------------------------------------------------------------ журнал прогонов
CREATE TABLE runs.run (
    id          bigserial PRIMARY KEY,
    script      text NOT NULL,
    git_commit  text,
    model       text,           -- модель, которой работал агент
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text NOT NULL DEFAULT 'идёт',
    environment jsonb,
    note        text
);

CREATE TABLE runs.run_input (
    id       bigserial PRIMARY KEY,
    run_id   bigint NOT NULL REFERENCES runs.run(id),
    kind     text NOT NULL,
    ref_id   bigint,
    ref_text text,
    checksum text
);

CREATE TABLE runs.metric (
    id     bigserial PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES runs.run(id),
    name   text NOT NULL,
    value  double precision,
    extra  jsonb
);

CREATE TABLE runs.artifact (
    id       bigserial PRIMARY KEY,
    run_id   bigint NOT NULL REFERENCES runs.run(id),
    kind     text,            -- 'снимок датасета', 'карта', 'отчёт'
    path     text NOT NULL,
    checksum text,
    note     text
);

-- ------------------------------------------------- мост к базе знаний (задел)
CREATE TABLE bridge.feature_concept (
    id           bigserial PRIMARY KEY,
    feature_id   integer NOT NULL REFERENCES data.feature(id),
    concept_code text NOT NULL,     -- понятие в базе знаний
    role         text,              -- 'прямой', 'прокси'
    origin       meta.origin NOT NULL,
    evidence_ref text,
    run_id       bigint REFERENCES runs.run(id),
    status       meta.status NOT NULL DEFAULT 'черновик',
    confirmed_by text,
    confirmed_at timestamptz,
    note         text
);
COMMENT ON TABLE bridge.feature_concept IS
    'Гипотеза «колонка измеряет понятие», а не справочник соответствий';
