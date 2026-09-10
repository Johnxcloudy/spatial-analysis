import hashlib
import os
import sqlite3
from pathlib import Path
from contextlib import closing

import pytest

from spatial_engine import vector_queries as queries
from spatial_engine.errors import DomainError
from test_vectors import import_path
from vector_fixtures import create_fixture_bundle


def trace(monkeypatch):
    statements = []
    connect = queries._connect
    def observed(path):
        connection = connect(path)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(queries, '_connect', observed)
    return statements


def sql_fixture(tmp_path, rows, pk='_sa_fid', id_field='_sa_id', definition='INTEGER PRIMARY KEY'):
    path = tmp_path / 'sql-unit.sqlite'
    with closing(sqlite3.connect(path)) as c:
        c.execute(f'CREATE TABLE features("{pk}" {definition}, "{id_field}" TEXT, label TEXT)')
        c.executemany('INSERT INTO features VALUES(?,?,?)', rows)
        c.commit()
    return {'id': 'fixture', 'version': 'test', 'kind': 'vector', 'featureCount': len(rows),
            'internalIdField': id_field, 'storageLayer': 'features',
            'fields': [{'name': 'label', 'storageType': 'string'}]}, path


def test_genuine_gpkg_default_page_uses_seek_without_sort_or_offset(tmp_path, monkeypatch):
    source = create_fixture_bundle(tmp_path / 'source')['gpkg']
    result = import_path(source, tmp_path / 'import')
    path, dataset = Path(result['artifactPath']), result['dataset']
    statements = trace(monkeypatch)
    page = queries.attribute_page(dataset, path, {'offset': 2, 'limit': 2})
    assert [r['id'] for r in page['rows']] == ['3', '4']
    assert page['total'] == 4 and not page['hasMore']
    selects = [sql for sql in statements if sql.startswith('SELECT')]
    assert not any(' OFFSET ' in sql for sql in selects)
    assert any('WHERE "_sa_fid" > 2' in sql for sql in selects)
    assert 'BEGIN' in statements


@pytest.mark.parametrize('rows', [
    [(10, '1', 'a'), (20, '2', 'b'), (30, '3', 'c')],
    [(1, '2', 'a'), (2, '1', 'b'), (3, '10', 'c')],
    [(1, '01', 'a'), (2, '2', 'b'), (3, '3', 'c')],
    [(1, None, 'a'), (2, '2', 'b'), (3, '3', 'c')],
    [(0, '1', 'a'), (1, '2', 'b'), (2, '3', 'c')],
    [(1, '1', 'a'), (2, '1x', 'b'), (3, '10', 'c')],
])
def test_noncertifiable_layout_preserves_numeric_offset_semantics(tmp_path, monkeypatch, rows):
    dataset, path = sql_fixture(tmp_path, rows)
    with closing(sqlite3.connect(path)) as c:
        expected = c.execute('SELECT _sa_id FROM features ORDER BY CAST(_sa_id AS INTEGER) LIMIT 2 OFFSET 1').fetchall()
    statements = trace(monkeypatch)
    result = queries.attribute_page(dataset, path, {'offset': 1, 'limit': 2})
    assert [r['id'] for r in result['rows']] == [str(r[0]) for r in expected]
    assert any(' OFFSET 1' in sql for sql in statements)


@pytest.mark.parametrize('offset', [0, 1, 9, 10, 11])
def test_seek_handles_renamed_ids_primary_key_empty_and_end(tmp_path, monkeypatch, offset):
    rows = [(i, str(i), None if i % 2 else '001') for i in range(1, 11)]
    dataset, path = sql_fixture(tmp_path, rows, pk='_sa_fid_1', id_field='_sa_id_1')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = queries.attribute_page(dataset, path, {'offset': offset, 'limit': 2, 'descending': True})
    assert [r['id'] for r in result['rows']] == [str(i) for i in range(offset + 1, min(offset + 3, 11))]
    assert result['total'] == 10 and result['hasMore'] == (offset + len(result['rows']) < 10)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_empty_and_metadata_count_mismatch_use_real_count(tmp_path):
    dataset, path = sql_fixture(tmp_path, [])
    dataset['featureCount'] = 20
    result = queries.attribute_page(dataset, path, {})
    assert result['rows'] == [] and result['total'] == 0 and not result['hasMore']


def test_missing_snapshot_keeps_query_failed_error(tmp_path):
    dataset, path = sql_fixture(tmp_path, [])
    path.unlink()
    with pytest.raises(DomainError) as error:
        queries.attribute_page(dataset, path, {})
    assert error.value.kind == 'query_failed'


def test_deadline_covers_certification_and_can_retry(tmp_path, monkeypatch):
    dataset, path = sql_fixture(tmp_path, [(i, str(i), 'a') for i in range(1, 10001)])
    monkeypatch.setattr(queries, 'SQL_SECONDS', 0)
    with pytest.raises(DomainError) as error:
        queries.attribute_page(dataset, path, {})
    assert error.value.kind == 'query_timeout'
    monkeypatch.setattr(queries, 'SQL_SECONDS', 1.5)
    result = queries.attribute_page(dataset, path, {'offset': 9999})
    assert [r['id'] for r in result['rows']] == ['10000']


def test_replaced_path_cannot_return_page_using_original_lease(tmp_path, monkeypatch):
    dataset, path = sql_fixture(tmp_path, [(1, '1', 'a')])
    original = path.read_bytes()
    row = queries._row
    def swapped(*args):
        path.rename(tmp_path / 'original.sqlite')
        path.write_bytes(original)
        return row(*args)
    monkeypatch.setattr(queries, '_row', swapped)
    with pytest.raises(DomainError, match='identity changed'):
        queries.attribute_page(dataset, path, {})


@pytest.mark.parametrize('params', [{'sortField': 'label'}, {'filter': {'field': 'label', 'operator': 'equals', 'value': 'a'}}])
def test_explicit_sort_filter_do_not_certify(tmp_path, monkeypatch, params):
    dataset, path = sql_fixture(tmp_path, [(1, '1', 'a'), (2, '2', None), (3, '3', 'a')])
    def forbidden(*args):
        pytest.fail('Certification is only for default unfiltered pages')
    monkeypatch.setattr(queries, '_certify_page_key', forbidden)
    result = queries.attribute_page(dataset, path, params)
    assert result['total'] == (2 if 'filter' in params else 3)


@pytest.mark.skipif(os.name != 'nt', reason='Windows mandatory read lease')
def test_write_is_denied_during_page_and_lease_released_after_error(tmp_path, monkeypatch):
    dataset, path = sql_fixture(tmp_path, [(1, '1', 'a')])
    connect = queries._connect
    def failing(p):
        with pytest.raises(PermissionError):
            with path.open('r+b') as stream:
                stream.write(b'x')
        raise sqlite3.OperationalError('injected read failure')
    monkeypatch.setattr(queries, '_connect', failing)
    with pytest.raises(DomainError, match='Could not read'):
        queries.attribute_page(dataset, path, {})
    monkeypatch.setattr(queries, '_connect', connect)
    with path.open('r+b'):
        pass
