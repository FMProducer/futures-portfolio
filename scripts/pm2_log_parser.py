#!/usr/bin/env python3
"""
pm2_log_parser.py
Парсит PM2 логи ботов и записывает промежуточную телеметрию в SQLite.

Источники:
  - C:\\Users\\svsma\\.pm2\\logs\\real-{ticker}-out-{pid}.log
  - C:\\Users\\svsma\\.pm2\\logs\\paper-{ticker}-out-{pid}.log

Целевые таблицы:
  - heartbeats (каждые 1-2 мин): TPV, PnL, позиции
  - rebalances: номер цикла, TPV
  - trades: BUY/SELL с ценами

Запуск:
  python scripts/pm2_log_parser.py --scan-all
  python scripts/pm2_log_parser.py --scan-all --dry-run
  python scripts/pm2_log_parser.py --ticker ZECUSDT --mode real
"""

import os
import re
import glob
import sqlite3
import datetime
import argparse
from typing import Dict, List, Optional, Tuple, Any

# === Paths ===
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "trading_stats.db")
PM2_LOGS_DIR = r"C:\Users\svsma\.pm2\logs"

# === Regex Patterns ===
HEARTBEAT_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO: Heartbeat: "
    r"TPV=([\d.]+) \| PnL=([+-]?[\d.]+)"
)
REBALANCE_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO: Rebalance #(\d+) complete\. TPV: ([\d.]+)"
)
TRADE_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO: 📝 (REAL|PAPER): "
    r"(BUY|SELL) ([\d.]+) (\w+)_(LONG|SHORT|VIRTUAL) @ ([\d.]+)"
)
GUARD_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ (INFO|WARNING): "
    r"(.*?(?:Guard|Trailing|Velocity|Spread|Trend).*?)$"
)


def get_db(db_path: str) -> sqlite3.Connection:
    """Подключение к БД с WAL mode."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables(conn: sqlite3.Connection):
    """Создаёт таблицы если их нет."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS heartbeats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            mode TEXT NOT NULL,
            tpv REAL,
            pnl REAL,
            UNIQUE(timestamp, ticker, mode)
        );
        
        CREATE TABLE IF NOT EXISTS rebalances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            mode TEXT NOT NULL,
            cycle INTEGER,
            tpv REAL,
            UNIQUE(timestamp, ticker, mode)
        );
        
        CREATE TABLE IF NOT EXISTS pm2_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            mode TEXT NOT NULL,
            side TEXT NOT NULL,
            qty REAL NOT NULL,
            pos_type TEXT NOT NULL,
            price REAL NOT NULL,
            UNIQUE(timestamp, ticker, mode, side, qty, price)
        );
        
        CREATE TABLE IF NOT EXISTS guards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            mode TEXT NOT NULL,
            level TEXT,
            message TEXT,
            UNIQUE(timestamp, ticker, mode)
        );
    """)


def parse_log_file(filepath: str, ticker: str, mode: str) -> Dict[str, List]:
    """Парсит один PM2 лог-файл."""
    result = {
        'heartbeats': [],
        'rebalances': [],
        'trades': [],
        'guards': []
    }
    
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                # Heartbeat
                hb = HEARTBEAT_PATTERN.search(line)
                if hb:
                    result['heartbeats'].append({
                        'timestamp': hb.group(1),
                        'ticker': ticker,
                        'mode': mode,
                        'tpv': float(hb.group(2)),
                        'pnl': float(hb.group(3))
                    })
                    continue
                
                # Rebalance
                rb = REBALANCE_PATTERN.search(line)
                if rb:
                    result['rebalances'].append({
                        'timestamp': rb.group(1),
                        'ticker': ticker,
                        'mode': mode,
                        'cycle': int(rb.group(2)),
                        'tpv': float(rb.group(3))
                    })
                    continue
                
                # Trade
                tr = TRADE_PATTERN.search(line)
                if tr:
                    try:
                        qty = float(tr.group(4))
                        price = float(tr.group(7))
                    except ValueError:
                        continue  # Skip malformed trade lines
                    result['trades'].append({
                        'timestamp': tr.group(1),
                        'ticker': ticker,
                        'mode': tr.group(2).lower(),
                        'side': tr.group(3),
                        'qty': qty,
                        'pos_type': tr.group(6),
                        'price': price
                    })
                    continue
                
                # Guard
                gd = GUARD_PATTERN.search(line)
                if gd:
                    result['guards'].append({
                        'timestamp': gd.group(1),
                        'ticker': ticker,
                        'mode': mode,
                        'level': gd.group(2),
                        'message': gd.group(3)[:200]  # Ограничиваем длину
                    })
    
    except Exception as e:
        print(f"  Error parsing {filepath}: {e}")
    
    return result


def save_to_db(conn: sqlite3.Connection, data: Dict[str, List]) -> Dict[str, int]:
    """Сохраняет данные в БД. Возвращает количество вставленных записей."""
    inserted = {
        'heartbeats': 0,
        'rebalances': 0,
        'trades': 0,
        'guards': 0
    }
    
    for hb in data['heartbeats']:
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO heartbeats (timestamp, ticker, mode, tpv, pnl) VALUES (?, ?, ?, ?, ?)",
                (hb['timestamp'], hb['ticker'], hb['mode'], hb['tpv'], hb['pnl'])
            )
            inserted['heartbeats'] += cur.rowcount
        except:
            pass
    
    for rb in data['rebalances']:
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO rebalances (timestamp, ticker, mode, cycle, tpv) VALUES (?, ?, ?, ?, ?)",
                (rb['timestamp'], rb['ticker'], rb['mode'], rb['cycle'], rb['tpv'])
            )
            inserted['rebalances'] += cur.rowcount
        except:
            pass
    
    for tr in data['trades']:
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO pm2_trades (timestamp, ticker, mode, side, qty, pos_type, price) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tr['timestamp'], tr['ticker'], tr['mode'], tr['side'], tr['qty'], tr['pos_type'], tr['price'])
            )
            inserted['trades'] += cur.rowcount
        except:
            pass
    
    for gd in data['guards']:
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO guards (timestamp, ticker, mode, level, message) VALUES (?, ?, ?, ?, ?)",
                (gd['timestamp'], gd['ticker'], gd['mode'], gd['level'], gd['message'])
            )
            inserted['guards'] += cur.rowcount
        except:
            pass
    
    return inserted


def scan_pm2_logs(pm2_dir: str, ticker: Optional[str] = None, mode: Optional[str] = None) -> List[Tuple[str, str, str]]:
    """
    Сканирует PM2 логи. Возвращает список (filepath, ticker, mode).
    """
    if not os.path.isdir(pm2_dir):
        print(f"PM2 logs dir not found: {pm2_dir}")
        return []
    
    log_files = []
    
    for fname in os.listdir(pm2_dir):
        # Пропускаем error логи
        if '-error' in fname:
            continue
        
        # Ищем out логи
        if not fname.endswith('.log'):
            continue
        
        # Парсим имя файла: {mode}-{ticker}-out-{pid}.log
        match = re.match(r"(real|paper)-(\w+)-out-\d+\.log", fname)
        if not match:
            continue
        
        file_mode = match.group(1)
        file_ticker = match.group(2)
        
        # Фильтр по ticker/mode
        # PM2 filenames use short tickers (e.g. "aave"), user may pass "AAVEUSDT"
        if ticker:
            # Normalize: strip USDT suffix for comparison
            user_ticker = ticker.upper().replace("USDT", "")
            if file_ticker.upper() != user_ticker:
                continue
        if mode and file_mode != mode:
            continue
        
        filepath = os.path.join(pm2_dir, fname)
        log_files.append((filepath, file_ticker, file_mode))
    
    return log_files


def main():
    parser = argparse.ArgumentParser(description="PM2 log parser → SQLite telemetry")
    parser.add_argument("--scan-all", action="store_true", help="Scan all PM2 logs")
    parser.add_argument("--ticker", help="Filter by ticker (e.g., ZECUSDT)")
    parser.add_argument("--mode", choices=["real", "paper"], help="Filter by mode")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written")
    parser.add_argument("--db", default=DB_PATH, help="SQLite database path")
    parser.add_argument("--pm2-dir", default=PM2_LOGS_DIR, help="PM2 logs directory")
    args = parser.parse_args()
    
    if not args.scan_all:
        parser.print_help()
        return
    
    today = datetime.date.today().strftime("%Y-%m-%d")
    print(f"[{today}] Scanning PM2 logs in: {args.pm2_dir}")
    
    log_files = scan_pm2_logs(args.pm2_dir, args.ticker, args.mode)
    print(f"Found: {len(log_files)} log files")
    
    if not log_files:
        print("No log files found.")
        return
    
    # Парсим все логи
    all_data = {
        'heartbeats': [],
        'rebalances': [],
        'trades': [],
        'guards': []
    }
    
    for filepath, ticker, mode in log_files:
        data = parse_log_file(filepath, ticker, mode)
        for key in all_data:
            all_data[key].extend(data[key])
    
    print(f"\nParsed:")
    print(f"  Heartbeats: {len(all_data['heartbeats'])}")
    print(f"  Rebalances: {len(all_data['rebalances'])}")
    print(f"  Trades: {len(all_data['trades'])}")
    print(f"  Guards: {len(all_data['guards'])}")
    
    if args.dry_run:
        print("\n=== DRY RUN — would insert ===")
        if all_data['heartbeats']:
            print(f"\nSample heartbeats:")
            for hb in all_data['heartbeats'][:3]:
                print(f"  {hb['timestamp']} {hb['ticker']} ({hb['mode']}): TPV={hb['tpv']}, PnL={hb['pnl']}")
        if all_data['rebalances']:
            print(f"\nSample rebalances:")
            for rb in all_data['rebalances'][:3]:
                print(f"  {rb['timestamp']} {rb['ticker']} ({rb['mode']}): Cycle #{rb['cycle']}, TPV={rb['tpv']}")
        if all_data['trades']:
            print(f"\nSample trades:")
            for tr in all_data['trades'][:3]:
                print(f"  {tr['timestamp']} {tr['ticker']} ({tr['mode']}): {tr['side']} {tr['qty']} @ {tr['price']}")
        return
    
    # Записываем в БД
    conn = get_db(args.db)
    ensure_tables(conn)
    
    inserted = save_to_db(conn, all_data)
    conn.commit()
    conn.close()
    
    print(f"\nInserted:")
    for key, count in inserted.items():
        print(f"  {key}: {count}")


if __name__ == "__main__":
    main()
