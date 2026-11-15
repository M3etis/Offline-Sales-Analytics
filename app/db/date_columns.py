"""Перевод строковых колонок дат в TIMESTAMP у уже загруженных наборов данных.

До исправления загрузка сохраняла даты строкой ('2026-09-08T00:00:00'). Модель видела
`date VARCHAR`, писала `date - INTERVAL 30 DAY` или `DATE_TRUNC('month', date)`, и запрос падал.
Новые загрузки сразу пишут TIMESTAMP; здесь — разовая миграция старых таблиц при старте.
Повторный запуск ничего не делает: переведённая колонка уже не VARCHAR.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Доля значений, которые должны распознаться как дата, чтобы колонку перевести
MIN_PARSED_SHARE = 0.95


def migrate_date_columns(conn) -> list[str]:
    """Перевести VARCHAR-колонки дат в TIMESTAMP. Возвращает список «таблица.колонка»."""
    converted: list[str] = []
    try:
        datasets = conn.execute(
            "SELECT id, table_name FROM datasets WHERE table_name IS NOT NULL AND table_name LIKE 'dataset_%'"
        ).fetchall()
    except Exception as e:
        logger.warning(f"Date migration skipped: {e}")
        return converted

    for dataset_id, table in datasets:
        try:
            cols = conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?", [table]
            ).fetchall()
        except Exception:
            continue
        changed = False
        for col, col_type in cols:
            if col_type != 'VARCHAR':
                continue
            quoted = '"' + col.replace('"', '""') + '"'
            # ISO и «дд.мм.гггг»: дату узнаём по значениям, имя колонки может быть любым. Год — строго
            # четыре цифры: %Y в DuckDB принимает и «24», и «05.01.24» становилось 0024 годом, а коды
            # вида «01.11.1» — датами 1-го года
            dotted = f"regexp_full_match(trim({quoted}), '\\d{{1,2}}\\.\\d{{1,2}}\\.\\d{{4}}( \\d{{1,2}}:\\d{{2}}(:\\d{{2}})?)?')"
            iso = f"regexp_full_match(trim({quoted}), '\\d{{4}}-\\d{{1,2}}-\\d{{1,2}}([ T].*)?')"
            parse = (f"CASE WHEN {iso} THEN TRY_CAST({quoted} AS TIMESTAMP) WHEN {dotted} THEN "
                     f"COALESCE(try_strptime(trim({quoted}), '%d.%m.%Y'), try_strptime(trim({quoted}), '%d.%m.%Y %H:%M:%S'), "
                     f"try_strptime(trim({quoted}), '%d.%m.%Y %H:%M')) END")
            try:
                # Сначала выборка: полный проход по каждой текстовой колонке на каждом старте дорог
                s_total, s_parsed = conn.execute(
                    f"SELECT COUNT(v), COUNT({parse.replace(quoted, 'v')}) FROM "
                    f"(SELECT {quoted} AS v FROM {table} WHERE {quoted} IS NOT NULL LIMIT 200)"
                ).fetchone()
                if not s_total or s_parsed / s_total < MIN_PARSED_SHARE:
                    continue
                total, parsed = conn.execute(f"SELECT COUNT({quoted}), COUNT({parse}) FROM {table}").fetchone()
                # Переводим, только если распознаны ВСЕ значения: миграция необратима, и «не оплачено»
                # в колонке «срок оплаты» иначе молча становилось пустым. Колонку с остатком текста
                # готовые расчёты и так разбирают как дату при чтении
                if not total or parsed < total:
                    continue
                conn.execute(f"ALTER TABLE {table} ALTER {quoted} TYPE TIMESTAMP USING {parse}")
                converted.append(f"{table}.{col}")
                changed = True
            except Exception as e:
                logger.warning(f"Date migration failed for {table}.{col}: {e}")
        if changed:
            # Манифест хранит типы и диапазоны колонок — без пересборки модель видела бы VARCHAR
            try:
                from app.services.preanalysis import preanalyze_dataset

                preanalyze_dataset(conn, dataset_id, table)
            except Exception as e:
                logger.warning(f"Manifest rebuild failed for {table}: {e}")
    if converted:
        logger.info(f"Date columns converted to TIMESTAMP: {', '.join(converted)}")
    return converted
