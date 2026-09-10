"""Publish bounded pytest diagnostics without overriding the pytest step outcome."""

import argparse
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def bounded(value, limit=800):
    text = ' '.join(str(value).split())
    return text if len(text) <= limit else text[:limit] + ' [truncated]'


def annotation(value):
    return value.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--junit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--outcome', choices=['success', 'failure', 'cancelled', 'skipped', 'unknown'], default='unknown')
    args = parser.parse_args()
    checkout = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=False)
    report = {
        'status': 'unavailable', 'outcome': args.outcome, 'counts': None,
        'failures': [], 'omitted_failures': 0,
        'context': {'checkout_head': bounded(checkout.stdout.strip(), 100) if checkout.returncode == 0 else 'unavailable',
                    **{key: bounded(os.environ.get(key, ''), 200) for key in
                       ('GITHUB_SHA', 'GITHUB_EVENT_NAME', 'GITHUB_HEAD_REF', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')}},
    }
    try:
        # Read only a bounded XML document; do not copy captured stdout/stderr into public summaries.
        with args.junit.open('rb') as stream:
            data = stream.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError('JUnit exceeds 16 MiB diagnostic parse budget')
        if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
            raise ValueError('JUnit DTD/entity declarations are unsupported')
        root = ET.fromstring(data)
        if root.tag not in ('testsuite', 'testsuites'):
            raise ValueError('Expected testsuite or testsuites root')
        counts = {'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0}
        failure_count = 0
        for case in root.iter('testcase'):
            counts['tests'] += 1
            for tag, key in [('failure', 'failures'), ('error', 'errors'), ('skipped', 'skipped')]:
                for entry in case.findall(tag):
                    counts[key] += 1
                    if tag == 'skipped':
                        continue
                    failure_count += 1
                    if len(report['failures']) < 20:
                        report['failures'].append({
                            'test': bounded(case.get('classname', '') + '::' + case.get('name', ''), 240),
                            'kind': tag,
                            'message': bounded(entry.get('message') or entry.text or '(no message)'),
                        })
        report.update(status='available', counts=counts, omitted_failures=failure_count - len(report['failures']))
    except (OSError, ET.ParseError, ValueError) as error:
        report['diagnostic'] = bounded(f'JUnit unavailable: {error}')

    lines = ['## Engine pytest diagnostics', '', '<pre>',
             html.escape(f'Original pytest step outcome: {args.outcome}'),
             html.escape(f'JUnit status: {report["status"]}'),
             html.escape(json.dumps(report['context'], ensure_ascii=True)),
             html.escape(f'Observed testcase counts: {report["counts"]}'),
             'Counts do not replace the original pytest exit status.']
    if report['status'] == 'unavailable':
        lines.append(html.escape(report['diagnostic']))
        print('::error title=Pytest diagnostics unavailable::' + annotation(report['diagnostic']))
    for failure in report['failures']:
        detail = f'{failure["kind"]}: {failure["test"]}: {failure["message"]}'
        lines.append(html.escape(detail))
        print('::error title=Pytest failure::' + annotation(detail))
    lines.extend([f'Omitted failure details: {report["omitted_failures"]}', '</pre>', '',
                  'Full JUnit is retained in the workflow artifact when pytest produced it.', ''])
    markdown = '\n'.join(lines)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')
    args.output.with_suffix('.md').write_text(markdown, encoding='utf-8')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(markdown)
    return 0 if report['status'] == 'available' else 1


if __name__ == '__main__':
    raise SystemExit(main())
