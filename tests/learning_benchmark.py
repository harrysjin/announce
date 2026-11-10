"""Measure unchanged analysis path and, when configured, durable edit latency."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import statistics
import time
from unittest.mock import patch

from backend.main import analyze_job
from test_numeric_api import numeric_job, NumericReplay


def run_analysis(configured):
    durations = []
    for _ in range(7):
        _, job = numeric_job('24 25% 2026년 4개 0.35초')
        job['_dictionary'] = NumericReplay()
        with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://unreachable/unused' if configured else ''}), \
             patch('backend.learning_db.connection', side_effect=AssertionError('analysis must not access the learning DB')):
            started = time.perf_counter()
            asyncio.run(analyze_job(job))
            durations.append((time.perf_counter() - started) * 1000)
    return {'median_ms': round(statistics.median(durations[1:]), 3), 'runs': 6, 'db_calls': 0}


def main():
    # Warm Kiwi once; never confuse model startup with storage overhead.
    run_analysis(False)
    result = {'analysis_without_database': run_analysis(False), 'analysis_with_database_configured': run_analysis(True)}
    if os.environ.get('LEARNING_TEST_DATABASE_URL'):
        from test_learning_postgres import LearningPostgresTests
        cls = LearningPostgresTests
        cls.setUpClass()
        obj = cls('test_selection_duplicate_retry_and_conflicting_key')
        try:
            obj.setUp()
            elapsed = []
            for _ in range(20):
                started = time.perf_counter(); obj.select(); elapsed.append((time.perf_counter() - started) * 1000)
            result['durable_selection'] = {'median_ms': round(statistics.median(elapsed), 3),
                                           'p95_ms': round(sorted(elapsed)[18], 3), 'runs': 20}
        finally:
            obj.tearDown(); cls.tearDownClass()
    else:
        result['durable_selection'] = {'measured': False, 'reason': 'dedicated PostgreSQL DSN unavailable'}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
