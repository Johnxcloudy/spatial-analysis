"""Local-only ordinary-vector acceptance; source values never enter reports.

Run with --source PATH --layer NAME --output NEW_DIRECTORY [--executable EXE].
Actual business analysis, repairs and GDB advanced semantics are outside scope.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import time
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import pyogrio
import shapely
from pyproj import CRS

Rpc = importlib.import_module('verify-vectors').Rpc


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(source):
    paths = sorted(source.rglob('*')) if source.is_dir() else (
        sorted(p for p in source.parent.iterdir() if p.stem.casefold() == source.stem.casefold())
        if source.suffix.lower() == '.shp' else [source])
    rows = []
    for path in paths:
        require(not path.is_symlink(), 'Source links are outside this acceptance scope')
        if path.is_file():
            before = path.stat()
            sha = digest(path)
            after = path.stat()
            require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), 'Source changed during inventory')
            rows.append({'path': str(path.relative_to(source if source.is_dir() else source.parent)),
                         'bytes': after.st_size, 'sha256': sha})
    return rows


def canonical(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise AssertionError('Non-finite attribute is outside current importer support')
    return value


def attribute_digest(values):
    # Scalar encodings keep null, text and numerical types distinct.
    return hashlib.sha256(json.dumps([canonical(x) for x in values], ensure_ascii=True,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def scan(path, layer, fields, *, source_fid=None, internal_id=None, numeric_field=None):
    """Bounded Arrow batches; keep only identity/content hashes and sort keys."""
    started = time.monotonic()
    rows, order, numeric = {}, [], []
    counts = {'features': 0, 'vertices': 0, 'invalid': 0, 'empty': 0, 'missing': 0,
              'maxGeometryVertices': 0, 'nullByField': {field: 0 for field in fields}}
    info = pyogrio.read_info(path, layer=layer)
    with pyogrio.open_arrow(path, layer=layer, return_fids=True, batch_size=256, use_pyarrow=True) as (meta, reader):
        geometry = meta.get('geometry_name') or 'wkb_geometry'
        fid = source_fid or meta['fid_column']
        for batch in reader:
            require(time.monotonic() - started < 900, 'Independent scan exceeded 900 seconds')
            columns = {name: batch[name].to_pylist() for name in [*fields, fid, geometry]}
            ids = batch[internal_id].to_pylist() if internal_id else None
            for index in range(len(batch)):
                counts['features'] += 1
                source_identity = str(columns[fid][index])
                key = hashlib.sha256(source_identity.encode()).hexdigest()
                require(key not in rows, 'Duplicate or missing source FID mapping')
                require(columns[fid][index] is not None, 'NULL source FID mapping')
                values = [columns[field][index] for field in fields]
                for field, value in zip(fields, values):
                    counts['nullByField'][field] += value is None
                wkb = columns[geometry][index]
                g = None if wkb is None else shapely.from_wkb(wkb, on_invalid='raise')
                missing, empty = g is None, bool(shapely.is_empty(g))
                count = int(shapely.get_num_coordinates(g))
                counts['vertices'] += count
                counts['maxGeometryVertices'] = max(counts['maxGeometryVertices'], count)
                counts['missing'] += missing
                counts['empty'] += empty
                counts['invalid'] += not missing and not empty and not bool(shapely.is_valid(g))
                # Normalize ring/component ordering only, with no coordinate repair,
                # precision rounding, dimensional conversion or topology alteration.
                geometry_bytes = b'NULL' if missing else shapely.to_wkb(shapely.normalize(g), byte_order=1)
                rows[key] = (attribute_digest(values), hashlib.sha256(geometry_bytes).hexdigest())
                order.append(key)
                if ids is not None:
                    require(str(ids[index]) == str(counts['features']), 'Internal ID order differs from import mapping')
                if numeric_field:
                    value = columns[numeric_field][index]
                    numeric.append((value, counts['features']))
                require(counts['features'] <= 500000 and counts['vertices'] <= 10000000,
                        'Independent scan reached current feature/vertex budget')
    require(counts['features'] == info['features'], 'Independent count differs from driver metadata')
    return {'counts': counts, 'rows': rows, 'order': order, 'numeric': numeric, 'crs': info['crs'],
            'durationSeconds': time.monotonic() - started}


class PrivateRpc(Rpc):
    """Reuse transport/deadlines, but never propagate raw responses to logs."""
    def call(self, method, params=None):
        try:
            return super().call(method, params)
        except Exception:
            raise AssertionError(f'RPC failed: {method}; response suppressed for privacy') from None

    def wait_task(self, path, task):
        deadline = time.monotonic() + 930
        while task['status'] == 'running':
            require(time.monotonic() < deadline, 'Task exceeded 930-second deadline')
            time.sleep(.1)
            task = self.call('task.get', {'path': path, 'taskId': task['id']})
        require(task['status'] == 'completed', f'Task terminal status: {task["status"]}; detail suppressed')
        return task


def checkpoint(output, report):
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')


@contextmanager
def step(output, report, name):
    event = {'name': name, 'ok': False}
    report['steps'].append(event)
    started = time.monotonic()
    try:
        yield event
        event['ok'] = True
    finally:
        event['durationSeconds'] = time.monotonic() - started
        checkpoint(output, report)
        print(json.dumps(event, ensure_ascii=True), flush=True)


def compare(reference, actual):
    require(reference['rows'] == actual['rows'], 'Source-FID keyed attribute/normalized-coordinate digests differ')
    require(reference['counts'] == actual['counts'], 'Geometry or NULL aggregates differ')
    require(CRS(reference['crs']).equals(CRS(actual['crs']), ignore_axis_order=True), 'CRS differs')


def verify(rpc, source, layer_name, output, report):
    with step(output, report, 'runtime_source_enumeration_and_independent_scan') as event:
        runtime = rpc.call('runtime.info')
        require(runtime['protocolVersion'] == 6 and runtime['engineVersion'] in {'0.6.0', '0.6.1', '0.6.2'}, 'Unexpected runtime version')
        report['runtime'] = {'engineVersion': runtime['engineVersion'], 'protocolVersion': runtime['protocolVersion'],
                             'packaged': runtime['packaged'], 'versions': runtime['versions']}
        info = pyogrio.read_info(source, layer=layer_name)
        fields = list(info['fields'])
        numeric_field = next((str(name) for name, dtype in zip(fields, info['dtypes'])
                              if str(dtype).startswith(('float', 'int', 'uint'))), None)
        inspection = rpc.call('source.inspect', {'sourcePath': str(source), 'encoding': None})
        match = next((item for item in inspection['layers'] if item['name'] == layer_name), None)
        require(match is not None and match['featureCount'] == info['features'], 'Public source enumeration differs')
        require(set(item['name'] for item in inspection['layers']) == set(pyogrio.list_layers(source)[:, 0]), 'Layer enumeration differs')
        reference = scan(source, layer_name, fields, numeric_field=numeric_field)
        require(reference['crs'] is not None and reference['counts']['features'] > 0, 'This acceptance requires a known-CRS nonempty ordinary vector')
        report['sourceSummary'] = {'driver': info['driver'], 'layerCount': len(inspection['layers']),
                                   'fields': [{'name': str(name), 'dtype': str(dtype)} for name, dtype in zip(fields, info['dtypes'])],
                                   'geometryType': info['geometry_type'], 'crs': info['crs'], 'counts': reference['counts']}
        event['features'] = reference['counts']['features']
    with step(output, report, 'real_import_and_independent_snapshot_roundtrip') as event:
        project = rpc.call('project.create', {'directory': str(output / 'project'), 'name': 'Local real vector acceptance'})
        path = project['projectPath']
        task = rpc.wait_task(path, rpc.call('vector.import', {'path': path, 'sourcePath': str(source),
                   'sourceLayer': layer_name, 'encoding': None, 'assignedCrs': None}))
        workspace = rpc.call('workspace.get', {'path': path})
        dataset = next(item for item in workspace['datasets'] if item['id'] == task['datasetId'])
        require([f['name'] for f in dataset['fields']] == fields, 'Imported field names/order differ')
        snapshot = Path(path).parent / dataset['relativePath']
        stored = scan(snapshot, dataset['storageLayer'], fields, source_fid=dataset['sourceFidField'], internal_id=dataset['internalIdField'])
        compare(reference, stored)
        require(dataset['featureCount'] == reference['counts']['features'], 'Dataset feature count differs')
        require(CRS(dataset['crsWkt']).equals(CRS(reference['crs']), ignore_axis_order=True), 'Dataset CRS differs')
        for key in ('features', 'vertices', 'invalid', 'empty', 'missing'):
            require(dataset['report']['counts'][key] == reference['counts'][key], 'Import quality aggregate differs: ' + key)
        invalid_check = next(x for x in dataset['report']['checks'] if x['code'] == 'geometry_valid')
        require(invalid_check['count'] == reference['counts']['invalid'], 'Invalid geometry report count differs')
        if reference['counts']['invalid']:
            require(not invalid_check['passed'] and dataset['report']['status'] == 'restricted', 'Invalid geometry was not reported as restricted')
        require(digest(snapshot) == dataset['version'], 'Snapshot version hash differs')
        report['projectPath'], report['datasetId'] = path, dataset['id']
        report['importSummary'] = {'status': dataset['report']['status'], 'counts': dataset['report']['counts'],
                                   'fields': dataset['fields'], 'snapshotBytes': snapshot.stat().st_size,
                                   'snapshotSha256': dataset['version'], 'notChecked': dataset['report']['notChecked']}
        event['invalidGeometryPreserved'] = reference['counts']['invalid']
    with step(output, report, 'first_last_pages_numeric_order_selection_and_viewport') as event:
        count = reference['counts']['features']
        # Force separate first and final pages even for small real fixtures.
        limit = min(200, max(1, count // 2))
        query = {'path': path, 'datasetId': dataset['id'], 'offset': 0, 'limit': limit,
                 'sortField': None, 'descending': False, 'filter': None}
        first = rpc.call('vector.page', query)
        last = rpc.call('vector.page', {**query, 'offset': max(0, count - limit)})
        for page, offset in ((first, 0), (last, max(0, count - limit))):
            require(page['total'] == count and len(page['rows']) == min(limit, count-offset), 'Page count differs')
            for position, row in enumerate(page['rows'], offset):
                require(row['id'] == str(position + 1), 'Page stable ID/order differs')
                values = [row['values'][field] for field in fields]
                # JS-unsafe integers are intentionally strings in the RPC contract.
                for index, field in enumerate(dataset['fields']):
                    if field['storageType'] in {'int64', 'uint64', 'integer'} and isinstance(values[index], str):
                        values[index] = int(values[index])
                require(attribute_digest(values) == reference['rows'][reference['order'][position]][0], 'Page attribute digest differs')
        require(not last['hasMore'], 'Final page incorrectly reports more rows')
        if numeric_field:
            for descending in (False, True):
                ordered = sorted(reference['numeric'], key=lambda pair: (pair[0] is None,
                    0 if pair[0] is None else -pair[0] if descending else pair[0], pair[1]))
                for offset in (0, max(0, count-limit)):
                    page = rpc.call('vector.page', {**query, 'sortField': numeric_field, 'descending': descending, 'offset': offset})
                    require([row['id'] for row in page['rows']] == [str(pair[1]) for pair in ordered[offset:offset+limit]], 'Numerical sort differs from source-derived order')
            event['numericSort'] = 'ascending_and_descending_first_last_passed'
        else:
            event['numericSort'] = 'not_applicable_no_numeric_field'
        feature = rpc.call('vector.feature', {'path': path, 'datasetId': dataset['id'], 'featureId': first['rows'][0]['id']})
        require(feature['row'] == first['rows'][0], 'Feature selection and table row differ')
        require(dataset['boundsWgs84'] is not None, 'No display bounds available for viewport acceptance')
        viewport = rpc.call('vector.viewport', {'path': path, 'datasetId': dataset['id'], 'bbox': dataset['boundsWgs84'],
                                               'limit': 2000, 'propertyFields': []})
        require(viewport['returnedCount'] <= 2000 and len(viewport['collection']['features']) == viewport['returnedCount'], 'Viewport exceeds response count bound')
        require(len(json.dumps(viewport).encode()) <= 2*1024*1024, 'Viewport exceeds response byte bound')
        if count <= 2000 and reference['counts']['vertices'] <= 100000 and not reference['counts']['missing'] and not reference['counts']['empty']:
            require(viewport['returnedCount'] == count and not viewport['truncated'], 'Small viewport omitted source features')
        event['viewportReturned'], event['viewportTruncated'] = viewport['returnedCount'], viewport['truncated']
    with step(output, report, 'real_export_and_independent_source_fid_roundtrip') as event:
        export = output / 'export.gpkg'
        rpc.wait_task(path, rpc.call('vector.export', {'path': path, 'datasetId': dataset['id'], 'destination': str(export)}))
        exported = scan(export, dataset['storageLayer'], fields, source_fid=dataset['sourceFidField'], internal_id=dataset['internalIdField'])
        compare(reference, exported)
        exported_info = rpc.call('source.inspect', {'sourcePath': str(export), 'encoding': None})
        require(exported_info['driver'] == 'GPKG', 'Export is not a genuine GeoPackage')
        event['features'], event['invalidGeometryPreserved'] = exported['counts']['features'], exported['counts']['invalid']
        report['exportSha256'] = digest(export)
    with step(output, report, 'close_reopen_save_as_and_snapshot_identity'):
        rpc.call('project.close')
        reopened = rpc.call('project.open', {'path': path})
        require(rpc.call('workspace.get', {'path': path})['datasets'] == workspace['datasets'], 'Reopen dataset metadata differs')
        require(rpc.call('vector.page', query) == first, 'Reopen page differs')
        copied = rpc.wait_task(path, rpc.call('project.saveAs', {'path': path, 'directory': str(output / 'copy'),
            'name': 'Local real vector copy', 'description': '', 'analysisCrs': reopened['analysisCrs'],
            'displayCrs': reopened['displayCrs'], 'viewState': reopened['viewState']}))
        copy_path = copied['destination']
        require(digest(Path(copy_path).parent / dataset['relativePath']) == dataset['version'], 'Copied snapshot hash differs')
        require(digest(snapshot) == dataset['version'], 'Original snapshot changed')
        rpc.call('project.close')
        copy_project = rpc.call('project.open', {'path': copy_path})
        require(copy_project['id'] != project['id'], 'Copy project identity was reused')
        require(rpc.call('workspace.get', {'path': copy_path})['datasets'] == workspace['datasets'], 'Copy dataset metadata differs')
        require(rpc.call('vector.page', {**query, 'path': copy_path}) == first, 'Copied page differs')
        rpc.call('project.close')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--layer', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--executable')
    args = parser.parse_args()
    source, output = Path(args.source).resolve(), Path(args.output).resolve()
    require(source.exists() and output != source and not output.is_relative_to(source), 'Output must be separate from source')
    output.mkdir(parents=True, exist_ok=False)
    report = {'ok': False, 'startedAt': datetime.now(UTC).isoformat(), 'steps': [],
              'privacy': 'Local-only artifacts; no attribute values, row geometries or source feature IDs in report/log output.',
              'comparison': 'Independent Arrow source-FID keyed SHA-256 of scalar values and normalized WKB; no repair or rounding.',
              'pending': ['GDB domains/subtypes/relationships/attachments/topology semantics', 'ArcGIS independent comparison',
                          'Formal area analysis: classification standard/field and study boundary not inferred',
                          'Survey accuracy, independent Windows and offline/upgrade acceptance']}
    before = inventory(source)
    (output / 'inventory-before.json').write_text(json.dumps(before, indent=2), encoding='utf8')
    rpc = PrivateRpc(Path(__file__).resolve().parents[1], output, args.executable)
    try:
        verify(rpc, source, args.layer, output, report)
        if args.executable:
            require(report['runtime']['packaged'], 'Requested executable is not packaged runtime')
        report['ok'] = True
    except Exception as exc:
        report['errorType'] = type(exc).__name__
        # Only our explicit aggregate assertion strings are safe to persist.
        report['error'] = str(exc) if isinstance(exc, AssertionError) else 'External exception details suppressed for privacy'
    finally:
        rpc.close()
        after = inventory(source)
        (output / 'inventory-after.json').write_text(json.dumps(after, indent=2), encoding='utf8')
        report['sourceIntegrity'] = {'files': len(before), 'bytes': sum(x['bytes'] for x in before), 'unchanged': before == after}
        report['ok'] = report['ok'] and before == after
        report['finishedAt'] = datetime.now(UTC).isoformat()
        checkpoint(output, report)
        print(json.dumps({'ok': report['ok'], 'report': str(output / 'report.json')}))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
