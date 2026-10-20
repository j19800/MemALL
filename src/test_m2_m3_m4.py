"""M2 / M3 / M4 regression checks (thread inference, source_ids, promotion).

Isolation: runs against a THROWAWAY database.  This suite writes memories
(deleting its own rows by project prefix) — running it against the
production store is what left 22 stray "test_agent" rows there.
"""
import os
import sys
import tempfile

sys.path.insert(0, '.')

# Isolate BEFORE importing memall (DB_PATH resolves at import time).
_TMP = tempfile.mkdtemp(prefix="memall_m2m3m4_")
os.environ["MEMALL_DB_PATH"] = os.path.join(_TMP, "test.db")

import memall.config as _mc  # noqa: E402
_mc.reset_config()
_sec = dict(_mc.get_config("security") or {})
_sec["disabled_agents"] = []
_sec["disabled_projects"] = []
_mc._config["security"] = _sec

from memall.core.thin_waist import _infer_thread_root, MemoryInput
from memall.core.models import Memory
from memall.core.db import get_pool
from memall.config import get_config
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager

@contextmanager
def _pool_conn():
    pool = get_pool()
    conn = pool.get()
    try:
        yield conn
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error as e:
            pass
        raise
    finally:
        try:
            conn.commit()
        except sqlite3.Error as e:
            pass
        pool.put(conn)

print('=== Testing M2: Thread ID Inference with Semantic Fallback ===')
print('Thread inference window: {} minutes'.format(get_config('capture.thread_inference_window_minutes', 0)))
print('Semantic fallback window: {} memories'.format(get_config('capture.thread_inference_semantic_window', 0)))
print('Semantic fallback threshold: {}'.format(get_config('capture.thread_inference_semantic_threshold', 0.7)))

# Test that the function still works for temporal case
with _pool_conn() as conn:
    # Clear any test data
    conn.execute('DELETE FROM memories WHERE project LIKE "ThreadTest%"')
    conn.commit()
    
    # Test basic temporal inference still works
    test_project = 'ThreadTestTemporal_' + str(int(datetime.now().timestamp()))
    agent_name = 'test_agent'
    category = 'test'
    
    # Create first memory
    data1 = MemoryInput(
        content='First memory for thread testing',
        agent_name=agent_name,
        category=category,
        project=test_project,
        level='P2'
    )
    from memall.core.thin_waist import _capture_normalize_and_validate
    data1 = _capture_normalize_and_validate(data1)
    now1 = datetime.now(timezone.utc).isoformat()
    from memall.core.thin_waist import _capture_insert_row, _capture_post_insert
    import hashlib
    def content_hash(content: str) -> str:
        return hashlib.sha256((content or '').encode('utf-8')).hexdigest()
    h1 = content_hash(data1.content)
    mem_id1 = _capture_insert_row(conn, data1, h1, now1)
    _capture_post_insert(conn, mem_id1, data1, h1)
    conn.commit()
    print('Created first memory ID: {}'.format(mem_id1))
    
    # Create second memory within temporal window
    data2 = MemoryInput(
        content='Second memory for thread testing',
        agent_name=agent_name,
        category=category,
        project=test_project,
        level='P2'
    )
    data2 = _capture_normalize_and_validate(data2)
    now2 = datetime.now(timezone.utc).isoformat()  # Same time, should be within window
    thread_id2 = _infer_thread_root(conn, data2, now2)
    print('Temporal window thread_id for second memory: {}'.format(thread_id2))
    
    # Test semantic fallback by creating memories outside temporal window but with similar content
    from datetime import timedelta
    now3 = (datetime.fromisoformat(now1.replace('Z', '+00:00')) + timedelta(minutes=61)).isoformat()  # Outside 60-min window
    data3 = MemoryInput(
        content='First memory for thread testing',  # Same content as first memory
        agent_name=agent_name,
        category=category,
        project=test_project,
        level='P2'
    )
    data3 = _capture_normalize_and_validate(data3)
    thread_id3 = _infer_thread_root(conn, data3, now3)
    print('Semantic fallback thread_id for third memory (similar content): {}'.format(thread_id3))
    
    # Clean up
    conn.execute('DELETE FROM memories WHERE project LIKE "ThreadTest%"')
    conn.commit()

print('')
print('=== Testing M3: Source IDs in Memory Model ===')
# Check that Memory model now has source_ids field
memory_fields = list(Memory.__annotations__.keys())
print('Memory fields: {}'.format(', '.join(sorted(memory_fields))))
if 'source_ids' in memory_fields:
    print('✓ source_ids field found in Memory model')
else:
    print('✗ source_ids field NOT found in Memory model')

# Check that MemoryInput also has source_ids field
from memall.core.models import MemoryInput
input_fields = list(MemoryInput.__annotations__.keys())
print('MemoryInput fields: {}'.format(', '.join(sorted(input_fields))))
if 'source_ids' in input_fields:
    print('✓ source_ids field found in MemoryInput model')
else:
    print('✗ source_ids field NOT found in MemoryInput model')

print('')
print('=== Testing M4: Heat-based Level Promotion ===')
# Check promotion configuration
promo_config = get_config('promotion', {})
print('Promotion enabled: {}'.format(promo_config.get('enabled', False)))
print('Promotion thresholds: {}'.format(len(promo_config.get('access_thresholds', {}))))

# Test that promote.py can be imported
try:
    from memall.pipeline.promote import promote_step, get_promotion_config
    print('✓ promote.py imported successfully')
    promo_info = get_promotion_config()
    print('Promotion config from function: enabled={}, thresholds={}'.format(
        promo_info.get('enabled', False), 
        len(promo_info.get('thresholds', {}))
    ))
except Exception as e:
    print('✗ Failed to import promote.py: {}'.format(e))

# Test that promote step is in pipeline
try:
    import memall.pipeline.pipeline as pipe
    steps = [name for name, _, _, _ in pipe._PIPELINE_STEPS]
    if 'promote' in steps:
        print('✓ promote step found in pipeline')
    else:
        print('✗ promote step NOT found in pipeline')
        print('Pipeline steps: {}'.format(', '.join(steps)))
except Exception as e:
    print('✗ Error checking pipeline: {}'.format(e))

print('')
print('=== All Tests Completed ===')