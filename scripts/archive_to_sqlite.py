#!/usr/bin/env python3
"""
archive_to_sqlite.py
Читает archive JSON из history/ и заполняет trading_stats.db.

Источники:
  - archive_*_<timestamp>.json (real sessions)
  - archive_*_<timestamp>_paper_state_*.json (paper sessions)

Целевые таблицы:
  - bot_sessions (сессии ботов)
  - daily_stats  (агрегация по дням)

Таблица trades НЕ заполняется — архивы не содержат уровень сделок.

Запуск:
  python scripts/archive_to_sqlite.py --scan-all
  python scripts/archive_to_sqlite.py --scan-all --dry-run
"""

import os
import re
import json
import sqlite3
import datetime
import argparse
from typing import Dict, List, Optional, Tuple, Any

# === Paths (относительно корня проекта) ===
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_DIR = os.path.join(PROJECT_ROOT, "history")
DB_PATH = os.path.join(PROJECT_ROOT, "trading_stats.db")


def get_db(db_path: str) -> sqlite3.Connection:
    """Подключение к БД с WAL mode."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def parse_archive_file(filepath: str) -> Optional[Dict[str, Any]]:
    """
    Парсит один archive JSON.
    Возвращает dict с полями для bot_sessions или None если невалидный.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    filename = os.path.basename(filepath)

    # Определяем тип: real или paper
    is_paper_state = "_paper_state_" in filename
    is_paper_shadow = "_paper_shadow_" in filename and not is_paper_state

    # Shadow файлы не содержат данных для сессий
    if is_paper_shadow:
        return None

    # Извлекаем timestamp из имени файла (10+ цифр после _)
    ts_match = re.search(r"_(\d{10,})", filename)
    if not ts_match:
        return None
    archive_ts = float(ts_match.group(1))

    if is_paper_state:
        # Paper state archive — плоская структура
        return _parse_paper_state(data, filename, archive_ts)
    else:
        # Real archive — вложенная структура
        return _parse_real_archive(data, filename, archive_ts)


def _parse_real_archive(data: Dict, filename: str, archive_ts: float) -> Optional[Dict]:
    """Парсит real archive (с вложенным state и paper_state)."""
    state = data.get("state", {})
    if not state:
        return None

    ticker = data.get("ticker", "UNKNOWN")
    started_at = state.get("started_at", 0)
    last_update = state.get("last_update", 0)
    initial_tpv = state.get("initial_tpv", 0)
    last_tpv = state.get("last_tpv", 0)
    last_profit = state.get("last_profit", 0)
    total_pnl_pct = state.get("total_pnl_pct", 0)
    rebalance_cycles = state.get("rebalance_cycles", 0)
    triggered = state.get("trailing_stop_triggered", False)

    if started_at <= 0 or last_update <= 0:
        return None

    duration_sec = int(last_update - started_at)
    start_time = datetime.datetime.fromtimestamp(started_at).strftime("%Y-%m-%d %H:%M:%S")
    end_time = datetime.datetime.fromtimestamp(last_update).strftime("%Y-%m-%d %H:%M:%S")
    end_date = datetime.datetime.fromtimestamp(last_update).strftime("%Y-%m-%d")

    return {
        "ticker": ticker,
        "mode": "real",
        "start_time": start_time,
        "end_time": end_time,
        "end_date": end_date,
        "duration_sec": duration_sec,
        "tpv_start": initial_tpv,
        "tpv_end": last_tpv,
        "pnl": last_profit,
        "pnl_pct": total_pnl_pct,
        "rebal_count": rebalance_cycles,
        "close_reason": "trailing_stop" if triggered else "completed",
    }


def _parse_paper_state(data: Dict, filename: str, archive_ts: float) -> Optional[Dict]:
    """Парсит paper_state archive (плоская структура)."""
    ticker = data.get("base_ticker", "UNKNOWN")
    started_at = data.get("started_at", 0)
    last_update = data.get("last_update", 0)
    initial_tpv = data.get("initial_tpv", 0)
    last_tpv = data.get("last_tpv", 0)
    last_profit = data.get("last_profit", 0)
    total_pnl_pct = data.get("total_pnl_pct", 0)
    rebalance_cycles = data.get("rebalance_cycles", 0)
    triggered = data.get("trailing_stop_triggered", False)

    if started_at <= 0 or last_update <= 0:
        return None

    duration_sec = int(last_update - started_at)
    start_time = datetime.datetime.fromtimestamp(started_at).strftime("%Y-%m-%d %H:%M:%S")
    end_time = datetime.datetime.fromtimestamp(last_update).strftime("%Y-%m-%d %H:%M:%S")
    end_date = datetime.datetime.fromtimestamp(last_update).strftime("%Y-%m-%d")

    return {
        "ticker": ticker,
        "mode": "paper",
        "start_time": start_time,
        "end_time": end_time,
        "end_date": end_date,
        "duration_sec": duration_sec,
        "tpv_start": initial_tpv,
        "tpv_end": last_tpv,
        "pnl": last_profit,
        "pnl_pct": total_pnl_pct,
        "rebal_count": rebalance_cycles,
        "close_reason": "trailing_stop" if triggered else "completed",
    }


def save_bot_session(conn: sqlite3.Connection, session: Dict) -> bool:
    """Сохраняет сессию в bot_sessions. Возвращает True если вставлено."""
    # Проверяем дубликат по (ticker, mode, start_time)
    existing = conn.execute(
        "SELECT id FROM bot_sessions WHERE ticker=? AND mode=? AND start_time=?",
        (session["ticker"], session["mode"], session["start_time"])
    ).fetchone()

    if existing:
        return False

    conn.execute(
        """
        INSERT INTO bot_sessions
        (ticker, mode, start_time, end_time, duration_sec, tpv_start, tpv_end, pnl, close_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session["ticker"],
            session["mode"],
            session["start_time"],
            session["end_time"],
            session["duration_sec"],
            session["tpv_start"],
            session["tpv_end"],
            session["pnl"],
            session["close_reason"],
        ),
    )
    return True


def save_daily_stat(conn: sqlite3.Connection, session: Dict) -> bool:
    """Сохраняет агрегацию по дню в daily_stats. Возвращает True если вставлено."""
    date = session["end_date"]

    # Проверяем дубликат по UNIQUE(date, ticker, mode)
    existing = conn.execute(
        "SELECT id FROM daily_stats WHERE date=? AND ticker=? AND mode=?",
        (date, session["ticker"], session["mode"])
    ).fetchone()

    if existing:
        return False

    conn.execute(
        """
        INSERT OR REPLACE INTO daily_stats
        (date, ticker, mode, tpv_start, tpv_end, pnl, pnl_pct, rebal_count,
         commission_usdt, max_drawdown_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            date,
            session["ticker"],
            session["mode"],
            session["tpv_start"],
            session["tpv_end"],
            session["pnl"],
            session["pnl_pct"],
            session["rebal_count"],
            0.0,  # commission_usdt — неизвестно из архивов
            0.0,  # max_drawdown_pct — неизвестно из архивов
        ),
    )
    return True


def scan_archives(history_dir: str) -> Tuple[List[Dict], List[str]]:
    """
    Сканирует history/ и возвращает (sessions, errors).
    """
    if not os.path.isdir(history_dir):
        return [], [f"History dir not found: {history_dir}"]

    sessions = []
    errors = []

    for fname in os.listdir(history_dir):
        if not fname.endswith(".json"):
            continue

        fpath = os.path.join(history_dir, fname)
        session = parse_archive_file(fpath)

        if session:
            sessions.append(session)
        elif "_paper_shadow_" not in fname:
            # Shadow файлы ожидаемо пустые, не логируем
            errors.append(f"Skipped (invalid): {fname}")

    return sessions, errors


def main():
    parser = argparse.ArgumentParser(description="Archive JSON → SQLite telemetry")
    parser.add_argument("--scan-all", action="store_true", help="Scan all archives")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written")
    parser.add_argument("--db", default=DB_PATH, help="SQLite database path")
    parser.add_argument("--history", default=HISTORY_DIR, help="History directory path")
    args = parser.parse_args()

    if not args.scan_all:
        parser.print_help()
        return

    today = datetime.date.today().strftime("%Y-%m-%d")
    print(f"[{today}] Scanning archives in: {args.history}")

    sessions, errors = scan_archives(args.history)

    if errors:
        print(f"\nWarnings ({len(errors)}):")
        for e in errors[:10]:
            print(f"  {e}")

    # Группируем по mode
    real_sessions = [s for s in sessions if s["mode"] == "real"]
    paper_sessions = [s for s in sessions if s["mode"] == "paper"]

    print(f"\nFound: {len(real_sessions)} real, {len(paper_sessions)} paper sessions")

    if args.dry_run:
        print("\n=== DRY RUN — would insert ===")
        print(f"\nbot_sessions: {len(sessions)} rows")
        print(f"daily_stats: {len(sessions)} rows")
        print(f"\nSample sessions:")
        for s in sessions[:5]:
            print(f"  {s['ticker']} ({s['mode']}): PnL={s['pnl']:+.4f} ({s['pnl_pct']:+.2f}%), "
                  f"cycles={s['rebal_count']}, {s['close_reason']}")
        return

    # Записываем в БД
    conn = get_db(args.db)
    inserted_sessions = 0
    inserted_daily = 0

    for session in sessions:
        if save_bot_session(conn, session):
            inserted_sessions += 1
        if save_daily_stat(conn, session):
            inserted_daily += 1

    conn.commit()
    conn.close()

    print(f"\nInserted: {inserted_sessions} bot_sessions, {inserted_daily} daily_stats")
    print(f"Skipped (duplicates): {len(sessions) - inserted_sessions} sessions")


if __name__ == "__main__":
    main()
