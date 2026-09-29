# -*- coding: utf-8 -*-
"""Проверка паспортов перед подтверждением человеком.

Смотрит три вещи, каждую машинно:

* **числа против данных** — покрытие паспорта пересчитывается по значениям
  признака в базе; пропуски обязаны быть дополнением покрытия;
* **пометки против решений проекта** — входы критериальной формулы должны
  стоять циркулярными, гравика и потенциальные поля — непереносимыми,
  служебные признаки покрытия не могут быть переносимыми;
* **текст против правил оформления** — пустые поля и заглушки, англоязычный
  жаргон, название средства разработки.

Геологическую правоту утверждений скрипт не проверяет — это и остаётся
человеку в панели. Запуск: ``python scripts/db_check_passports.py``.
"""
from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

import sqlalchemy as sa

from gisdb import db

ЦИРКУЛЯРНЫЕ_ГРУППЫ = ('geo', 'geo2')          # прямые входы критериальной формулы
# расстояния и плотности, считанные по тем же факторным слоям формулы
ЦИРКУЛЯРНЫЕ_КОДЫ = {'dist_tect1', 'dist_tect2', 'dist_struct', 'dist_facies',
                    'dist_magm', 'dist_paleo', 'dens_tect', 'dens_magm'}
НЕПЕРЕНОСИМЫЕ_ГРУППЫ = ('gm', 'pf')            # гравика и потенциальные поля
СЛУЖЕБНЫЕ = re.compile(r'_(n_obs|valid_frac)$')
ЖАРГОН = re.compile(r'\b(honest|importance|permutation|feature|dataset|score|baseline|'
                    r'threshold|pipeline|leakage|target|train|test)\b', re.I)
ЧЕСТНЫЙ = re.compile(r'честн', re.I)
СРЕДСТВО = re.compile(r'claude|anthropic|opus|sonnet|cursor|copilot', re.I)
ЗАГЛУШКА = re.compile(r'^Признак группы|^не установлен[оа]?\s*$', re.I)

дефекты = collections.defaultdict(list)


def добавить(вид, что, подробно=''):
    дефекты[вид].append((что, подробно))


with db.engine('gis_read').connect() as c:
    def q(s, **p):
        return c.execute(sa.text(s), p).fetchall()

    print('ячеек по сеткам:', dict(q('SELECT grid_id, count(*) FROM data.cell GROUP BY 1')))

    строки = q("""
        SELECT f.id, f.code, f.group_code, f.definition->>'сборка' AS сборка,
               p.geological_meaning, p.kb_reference, p.aggregation_rule,
               p.coverage_frac, p.missing_frac, p.footprint_dependent,
               p.circularity, p.transferability::text AS переносимость,
               p.importance_checked, p.model, p.status
          FROM data.feature f JOIN meta.feature_passport p ON p.feature_id = f.id
         ORDER BY f.code""")
    print('паспортов признаков:', len(строки))

    факт = {номер: (доля, пусто) for номер, доля, пусто in q("""
        SELECT v.feature_id,
               count(v.value)::float8 / max(g.всего),
               (count(*) - count(v.value))::float8 / max(g.всего)
          FROM data.feature_value v
          JOIN data.cell cl ON cl.id = v.cell_id
          JOIN (SELECT grid_id, count(*) AS всего FROM data.cell GROUP BY 1) g
               ON g.grid_id = cl.grid_id
         GROUP BY v.feature_id""")}
    print('признаков со значениями:', len(факт))

    for r in строки:
        код, группа = r.code, r.group_code
        if r.id in факт:
            доля, пусто = факт[r.id]
            if r.coverage_frac is None or abs(r.coverage_frac - доля) > 0.005:
                добавить('покрытие расходится с данными', код,
                         'в паспорте %s, по значениям %.4f' % (r.coverage_frac, доля))
            # пропуски в паспорте считаются от полной сетки (ячейка без строки —
            # тоже пропуск), поэтому сверяются не с долей null среди строк,
            # а с дополнением покрытия — проверка ниже
        else:
            добавить('значений в базе нет', код, 'паспорт есть, ряда значений нет')

        if r.coverage_frac is not None and r.missing_frac is not None:
            if abs(r.coverage_frac + r.missing_frac - 1) > 0.005:
                добавить('покрытие и пропуски не дают единицу', код,
                         '%s + %s' % (r.coverage_frac, r.missing_frac))
        if r.coverage_frac is not None:
            if r.coverage_frac < 0.999 and not r.footprint_dependent:
                добавить('покрытие неполное, зависимость от контура снята', код,
                         'покрытие %.3f' % r.coverage_frac)
            # полное покрытие у съёмочной группы зависимость от контура не
            # снимает: правило db_fill_passports — «съёмка зависит от контура
            # всегда», покрытие 1.0 говорит лишь о том, что контур накрыл лист
        циркулярный = группа in ЦИРКУЛЯРНЫЕ_ГРУППЫ or код in ЦИРКУЛЯРНЫЕ_КОДЫ
        if циркулярный and not r.circularity:
            добавить('вход формулы не помечен циркулярным', код, 'группа ' + группа)
        if not циркулярный and r.circularity:
            добавить('помечен циркулярным вне входов формулы', код, 'группа ' + группа)
        if группа in НЕПЕРЕНОСИМЫЕ_ГРУППЫ and r.переносимость != 'непереносим':
            добавить('решение проекта: группа непереносима', код,
                     'группа %s, в паспорте «%s»' % (группа, r.переносимость))
        if СЛУЖЕБНЫЕ.search(код) and r.переносимость == 'переносим':
            добавить('служебный признак помечен переносимым', код, r.переносимость)

        for поле, значение in (('смысл', r.geological_meaning),
                               ('обоснование', r.kb_reference),
                               ('правило сведения', r.aggregation_rule)):
            if not (значение or '').strip():
                добавить('пустое поле: ' + поле, код)
            elif ЗАГЛУШКА.match((значение or '').strip()):
                добавить('заглушка в поле: ' + поле, код, (значение or '')[:60])
        текст = ' '.join(filter(None, (r.geological_meaning, r.kb_reference,
                                       r.aggregation_rule)))
        if ЖАРГОН.search(текст):
            добавить('англоязычный жаргон в тексте', код, ЖАРГОН.search(текст).group(0))
        if ЧЕСТНЫЙ.search(текст):
            добавить('слово «честный» в тексте', код)
        if СРЕДСТВО.search(текст) or СРЕДСТВО.search(r.model or ''):
            добавить('упоминание средства разработки', код, (r.model or '')[:40])

    слои = q("""SELECT l.code, p.source_note, p.method, p.footprint_note, p.model,
                       p.license_id, p.status
                  FROM meta.layer_passport p JOIN data.source_layer l ON l.id = p.layer_id""")
    print('паспортов слоёв:', len(слои))
    for s in слои:
        for поле, значение in (('описание', s.source_note), ('метод', s.method),
                               ('контур', s.footprint_note)):
            if not (значение or '').strip():
                добавить('слой, пустое поле: ' + поле, s.code)
        if s.license_id is None:
            добавить('слой без лицензии', s.code)
        текст = ' '.join(filter(None, (s.source_note, s.method, s.footprint_note)))
        if СРЕДСТВО.search(текст) or СРЕДСТВО.search(s.model or ''):
            добавить('слой: упоминание средства разработки', s.code, (s.model or '')[:40])
        if ЖАРГОН.search(текст):
            добавить('слой: англоязычный жаргон', s.code, ЖАРГОН.search(текст).group(0))

    связи = q("""SELECT b.id, f.code, b.concept_code AS фактор, b.role, b.origin,
                        b.evidence_ref, b.status
                   FROM bridge.feature_concept b
                   JOIN data.feature f ON f.id = b.feature_id
                  WHERE b.status = 'черновик'""")
    print('связей-черновиков:', len(связи))
    по_признаку = collections.defaultdict(set)
    for s in связи:
        по_признаку[s.code].add(s.фактор)
        if not (s.origin or '').strip():
            добавить('связь без источника', '%s -> %s' % (s.code, s.фактор))
        if not (s.evidence_ref or '').strip():
            добавить('связь без обоснования', '%s -> %s' % (s.code, s.фактор))
    for код, факторы in по_признаку.items():
        if len(факторы) > 1:
            добавить('признак связан с двумя факторами', код, ', '.join(sorted(факторы)))

print()
print('=' * 70)
if not дефекты:
    print('дефектов не найдено')
for вид, список in sorted(дефекты.items(), key=lambda kv: -len(kv[1])):
    print('\n[%d] %s' % (len(список), вид))
    for что, подробно in список[:12]:
        print('   ', что, ('- ' + подробно) if подробно else '')
    if len(список) > 12:
        print('     ... и ещё %d' % (len(список) - 12))
