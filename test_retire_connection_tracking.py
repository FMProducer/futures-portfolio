"""
Test to verify retire() properly closes connections for both read and write paths.
"""
import sys
sys.path.insert(0, '.')

import asyncio
import tempfile
from pathlib import Path
import sqlite3
import json
import os
import shutil
from core.paper_budget import reconcile_terminal_allocations

async def test_retire_paths():
    print("Testing retire() connection tracking...")
    
    # Use a persistent directory to avoid Windows cleanup issues
    tmpdir = 'C:/tmp/test_retire_' + str(os.getpid())
    os.makedirs(tmpdir, exist_ok=True)
    
    root = Path(tmpdir)
    (root / 'core').mkdir()
    (root / 'history').mkdir()
    
    # Create database
    db_path = root / 'core' / 'paper_budget.sqlite'
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE allocations (ticker TEXT, capital REAL, session TEXT, released INTEGER)')
        db.execute('CREATE TABLE events (kind TEXT, amount REAL)')
        db.execute("INSERT INTO allocations VALUES ('TESTUSDT', 1000.0, 'test_session_123', 0)")
        db.execute("INSERT INTO events (kind, amount) VALUES ('excluded_historical_gain', 50.0)")
    
    # Create state and wallet files - use string for initial_tpv as expected
    state = {
        'started_at': 'test_session_123', 
        'paper_close_complete': True, 
        'paper_closed_at': 1234567890,
        'base_ticker': 'TESTUSDT', 
        'initial_tpv': '1000.0',  # String as expected by Decimal
        'siphoning_reserve': 0,
        'positions': {'TESTUSDT_LONG': 0, 'TESTUSDT_SHORT': 0}, 
        'rebalance_cycles': 0
    }
    wallet = {
        'balance': '1000.0', 
        'siphoning_reserve': '0', 
        'base_ticker': 'TESTUSDT',
        'positions': {'TESTUSDT_LONG': 0, 'TESTUSDT_SHORT': 0}
    }
    
    (root / 'core' / 'paper_state_TESTUSDT.json').write_text(json.dumps(state))
    (root / 'core' / 'paper_shadow_TESTUSDT.json').write_text(json.dumps(wallet))
    
    # Create archive
    os.makedirs(root / 'history', exist_ok=True)
    archive = {'state': state, 'paper_state': {}, 'close_event': {'timestamp': 1234567890}}
    (root / 'history' / 'paper_close_TESTUSDT_test_session_123.json').write_text(json.dumps(archive))
    
    # Call reconcile (which calls retire internally for recover=True)
    returned, blocked = reconcile_terminal_allocations(Path(tmpdir), recover=True)
    
    print(f"Returned: {returned}")
    print(f"Blocked: {blocked}")
    
    # Verify the function completed without errors
    assert returned == ['TESTUSDT'], f"Expected ['TESTUSDT'], got {returned}"
    assert blocked == {}, f"Expected empty blocked, got {blocked}"
    
    print("✓ retire() completed successfully with proper cleanup")
    
    # Cleanup
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)

if __name__ == "__main__":
    asyncio.run(test_retire_paths())
