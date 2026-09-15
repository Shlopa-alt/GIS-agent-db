-- Собственная система координат проекта.
-- Сетки ГИС Интегро заданы proj4 из sidecar-файлов `*_pgrid.pj4`:
-- поперечная Меркатора на эллипсоиде Красовского, осевой меридиан 105°
-- (зона 18 Гаусса-Крюгера, СК-42, со сдвигом на WGS84).
-- В базе получает SRID 990018: 99 — признак «наш», 0018 — номер зоны.

DELETE FROM spatial_ref_sys WHERE srid = 990018;
INSERT INTO spatial_ref_sys (srid, auth_name, auth_srid, proj4text, srtext) VALUES (
    990018, 'GIS-AU-U', 18,
    '+proj=tmerc +lon_0=105 +lat_0=0 +x_0=500000 +y_0=0 +k_0=1 '
    '+towgs84=23.57,-140.95,-79.8,0,0.35,0.79,-0.22 +ellps=krass +units=m +no_defs',
    ''
);
