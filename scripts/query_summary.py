"""Quick summary query for trading_stats.db."""
import sqlite3

conn = sqlite3.connect(r'C:\Python\Prosperous_Bot\futures_portfolio\trading_stats.db')
c = conn.cursor()
tables = ['bot_sessions', 'daily_stats', 'heartbeats', 'rebalances', 'pm2_trades', 'guards']
for t in tables:
    c.execute(f'SELECT COUNT(*) FROM {t}')
    cnt = c.fetchone()[0]
    print(f'{t}: {cnt:,} rows')
conn.close()
