"""Bounded source/frozen default pagination acceptance on copies of existing fixtures.

No new analysis runs; new connections/processes are NOT an OS cold-cache test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from spatial_engine import vector_queries as queries
from spatial_engine.query_runner import QueryRunner
from spatial_engine.publication import ArtifactLease
from spatial_engine import __version__


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def row_hash(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def sql_page(dataset, path, offset, optimized):
    q = queries._quote
    columns = ','.join(q(name) for name in [dataset['internalIdField'], *[field['name'] for field in dataset['fields']]])
    table = q(dataset['storageLayer'])
    started = time.perf_counter()
    with closing(ArtifactLease(path)) as lease, closing(queries._connect(path)) as connection:
        connection.execute('BEGIN')
        phase = time.perf_counter()
        if optimized:
            key, total = queries._certify_page_key(connection, dataset)
            assert key is not None, 'Fixture must meet actual seek certification'
            sql = f'SELECT {columns} FROM {table} WHERE {key}>? ORDER BY {key} LIMIT 200'
            params = (offset,)
        else:
            total = connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
            sql = f'SELECT {columns} FROM {table} ORDER BY CAST({q(dataset["internalIdField"])} AS INTEGER) LIMIT 200 OFFSET ?'
            params = (offset,)
        first_ms = (time.perf_counter() - phase) * 1000
        plan = [list(row) for row in connection.execute('EXPLAIN QUERY PLAN ' + sql, params)]
        phase = time.perf_counter()
        records = connection.execute(sql, params).fetchall()
        page_ms = (time.perf_counter() - phase) * 1000
        rows = [queries._row(dataset, row) for row in records]
        lease.require_identity(path)
    return {'countOrCertificationMs': first_ms, 'pageMs': page_ms,
            'totalMs': (time.perf_counter() - started) * 1000, 'total': total,
            'rowHash': row_hash(rows), 'plan': plan, 'returned': len(rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', '--fixtures', dest='fixture', required=True, help='Existing verify-analysis.py report.json')
    parser.add_argument('--output', required=True, help='New directory; existing outputs are never overwritten')
    parser.add_argument('--executable', help='Frozen engine; omitted uses source query worker')
    parser.add_argument('--counts', type=int, nargs='+', default=[100000, 250000, 500000])
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.workers <= 5 or len(args.counts) != len(set(args.counts)):
        parser.error('workers must be 1..5 and counts distinct')
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'ok': False, 'startedAt': datetime.now(UTC).isoformat(), 'fixture': str(Path(args.fixture).resolve()),
              'executable': args.executable, 'thresholds': {'sqlSeconds': 1.5, 'workerSeconds': 2, 'workerMemoryGiB': 1},
              'limitations': ['New connections/workers are not OS cold-cache tests.',
                  'Copying, SHA verification and SQL comparison warm filesystem cache.',
                  'Per-page certification still scans; only the page fetch becomes a primary-key seek.',
                  'Detailed SQL phases use source module; source/frozen worker timings are separately recorded.',
                  'No automatic retry of failed query and no increased deadline.'], 'datasets': []}
    if args.executable:
        report['executableSha256'] = sha(Path(args.executable).resolve())
    try:
        fixture = json.loads(Path(args.fixture).read_text(encoding='utf8'))
        assert fixture['ok'] is True
        report['fixtureRuntime'] = fixture.get('runtime')
        report['sourceModuleVersion'] = __version__
        report['workerMode'] = 'frozen executable identified by sha256' if args.executable else 'current source engine'
        for count in args.counts:
            level = next(item for item in fixture['levels'] if item.get('featureCount') == count)
            project = Path(level['projectPath'])
            with closing(sqlite3.connect(project.as_uri() + '?mode=ro', uri=True)) as connection:
                datasets = [json.loads(row[0]) for row in connection.execute('SELECT dataset_json FROM datasets')]
            selected = [next(d for d in datasets if d['id'] == level[key]) for key in ['inputDatasetId', 'resultDatasetId']]
            for role, dataset in zip(['input', 'result'], selected):
                directory = output / f'{count}-{role}'
                directory.mkdir()
                original = project.parent / dataset['relativePath']
                original_hash = sha(original)
                copied = directory / 'snapshot.gpkg'
                shutil.copyfile(original, copied)
                entry = {'features': count, 'role': role, 'source': str(original), 'shaBefore': original_hash,
                         'sql': [], 'workerQueries': [], 'warmupMs': [], 'errors': []}
                report['datasets'].append(entry)
                try:
                    expected = {}
                    for offset in [0, count - 200]:
                        for trial in range(args.workers):
                            baseline = sql_page(dataset, copied, offset, False)
                            optimized = sql_page(dataset, copied, offset, True)
                            assert baseline['rowHash'] == optimized['rowHash'] and baseline['total'] == optimized['total'] == count
                            expected[offset] = baseline['rowHash']
                            entry['sql'].append({'offset': offset, 'trial': trial, 'baseline': baseline, 'optimized': optimized})
                    for trial in range(args.workers):
                        command = (lambda: [str(Path(args.executable).resolve()), '--query-worker']) if args.executable else None
                        start = time.perf_counter()
                        runner = QueryRunner(command_factory=command)
                        try:
                            assert runner._ready.wait(30), 'Bounded query worker warmup failed'
                            entry['warmupMs'].append((time.perf_counter() - start) * 1000)
                            for offset in [0, count - 200]:
                                start = time.perf_counter()
                                page = runner.execute('vector.page', {'dataset': dataset, 'managedPath': str(copied),
                                                                     'params': {'offset': offset, 'limit': 200, 'sortField': None, 'filter': None, 'descending': False}})
                                elapsed = (time.perf_counter() - start) * 1000
                                assert page['total'] == count and page['offset'] == offset and len(page['rows']) == 200
                                assert page['hasMore'] == (offset + 200 < count)
                                assert row_hash(page['rows']) == expected[offset]
                                entry['workerQueries'].append({'worker': trial, 'offset': offset, 'durationMs': elapsed, 'ok': True})
                        finally:
                            runner.close()
                except Exception as exc:
                    entry['errors'].append(repr(exc))
                    raise
                finally:
                    entry['shaAfter'] = sha(original)
                    entry['copyShaAfter'] = sha(copied)
                    entry['unchanged'] = entry['shaAfter'] == entry['copyShaAfter'] == original_hash
                    assert entry['unchanged'], 'Original/copy snapshot changed'
        timings = [q['durationMs'] for d in report['datasets'] for q in d['workerQueries']]
        report['workerSummary'] = {'count': len(timings), 'p95Ms': float(np.percentile(timings, 95)), 'maxMs': max(timings), 'failures': 0}
        report['ok'] = True
    except Exception as exc:
        report['error'] = repr(exc)
        raise
    finally:
        report['finishedAt'] = datetime.now(UTC).isoformat()
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf8')
        print(json.dumps({'ok': report['ok'], 'report': str(output / 'report.json'), 'summary': report.get('workerSummary'), 'error': report.get('error')}))


if __name__ == '__main__':
    main()
