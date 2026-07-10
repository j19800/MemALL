"""Query memory 5563 from MemALL database."""
import sys
sys.path.insert(0, r'E:\MemALL\src')
from memall.core.db import get_conn

conn = get_conn()
try:
    r = conn.execute('SELECT * FROM memories WHERE id = ?', (5563,)).fetchone()
    if r:
        d = dict(r)
        for k, v in d.items():
            print(f'  {k}: {v}')
    else:
        print('Memory 5563 not found')

    # Also show latest IDs to check what range
    recent = conn.execute('SELECT id, content[:80], level, agent_name, category FROM memories ORDER BY id DESC LIMIT 5').fetchall()
    print('\n--- Recent 5 memories ---')
    for row in recent:
        print(dict(row))
finally:
    conn.close()