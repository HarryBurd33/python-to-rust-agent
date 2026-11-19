#!/usr/bin/env python3
"""Restore exact snapshots and run once on a fresh macOS arm64 runner."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import zlib

CONTRACT = 'c0e4df120ec9c00e8047c1bb9b6eaffcb28c39064b69de211fa529703478bc76'
EVALUATOR = 'c9c049ee96b2d0613c4cdc44db100e7e3c1d44a9be160f4831f97764d0410cc2'


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def restore(destination):
    package = json.loads(Path(__file__).with_name('bundle.json').read_text())
    destination.mkdir(parents=True, exist_ok=False)
    for name, record in package['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe bundle path')
        content = zlib.decompress(base64.b64decode(record['zlib_base64'], validate=True))
        if len(content) != record['size'] or hashlib.sha256(content).hexdigest() != record['sha256']:
            raise ValueError('Snapshot hash mismatch: ' + name)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    assert hashlib.sha256((destination / 'contract.json').read_bytes()).hexdigest() == CONTRACT
    assert hashlib.sha256((destination / 'optimize.py').read_bytes()).hexdigest() == EVALUATOR
    return package


def capture(root, name, argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=45)
    (root / name).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError('Context command failed: ' + repr(argv))
    return result.stdout


def ready(text):
    idle = [float(v) for v in re.findall(r'([0-9.]+)% idle', text)]
    swaps = [list(map(int, v)) for v in re.findall(r'\d+\((\d+)\) swapins, \d+\((\d+)\) swapouts', text)]
    return {'idle_percent': idle, 'swap_intervals': swaps,
            'passed': len(idle) == 6 and len(swaps) == 6
            and all(v >= 85 for v in idle[1:]) and all(v == [0, 0] for v in swaps[1:])}


def calibrate(root):
    rows = []
    for size in (0, 64, 128):
        output = root / ('memory-probe-' + str(size) + '.json')
        with output.open('w') as stream:
            child = subprocess.Popen([sys.executable, str(root / 'memory-probe.py'), str(size)], stdout=stream, start_new_session=True)
            def expired(signum, frame):
                os.killpg(child.pid, signal.SIGKILL)
                raise TimeoutError('Memory calibration timed out')
            previous = signal.signal(signal.SIGALRM, expired)
            signal.alarm(20)
            try:
                _, status, usage = os.wait4(child.pid, 0)
                child.returncode = os.waitstatus_to_exitcode(status)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, previous)
            if child.returncode:
                raise RuntimeError('Memory probe failed')
        rows.append({'allocated_mib': size, 'wait4_ru_maxrss_bytes': usage.ru_maxrss,
                     'child': json.loads(output.read_text())})
    save(root / 'memory-calibration.json', {'platform': platform.platform(), 'rows': rows,
         'note': 'Evidence for independent scope review; not an automatic attestation.'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    root = args.destination.resolve()
    package = restore(root)
    save(root / 'snapshot-manifest.json', {k: {'sha256': v['sha256'], 'size': v['size']} for k, v in package['files'].items()})
    if args.verify_only:
        print('Verified exact bundle:', len(package['files']), 'files; no benchmark ran')
        return
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('Requires the predeclared macOS arm64 runner')
    if sys.version_info[:3] != (3, 15, 0):
        raise RuntimeError('Requires Python 3.15.0')
    rust = capture(root, 'rust-version.txt', ['rustc', '--version', '--verbose'])
    if not rust.startswith('rustc 1.99.1 '):
        raise RuntimeError('Requires rustc 1.99.1')
    capture(root, 'python-version.txt', [sys.executable, '--version'])
    save(root / 'runner.json', {k: os.environ.get(k) for k in ('RUNNER_OS', 'RUNNER_ARCH', 'ImageOS', 'ImageVersion', 'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')})
    calibrate(root)
    text = capture(root, 'host-before.txt', ['top', '-l', '6', '-s', '2', '-n', '8', '-o', 'cpu', '-stats', 'pid,command,cpu'])
    readiness = ready(text)
    save(root / 'readiness.json', readiness)
    if not readiness['passed']:
        raise RuntimeError('Fresh VM did not meet readiness; no performance run started')
    common = [sys.executable, str(root / 'optimize.py')]
    arguments = [str(root / 'contract.json'), '--runs', str(root / 'runs'), '--expect-contract', CONTRACT]
    # Serial full workflows. No monitoring, profiler, batching or retries during timing.
    for action in ('baseline', 'evaluate'):
        command = common + [action] + arguments
        if action == 'evaluate':
            command += ['--candidate', str(root / 'candidate')]
        with (root / (action + '-result.json')).open('w') as out, (root / (action + '-stderr.txt')).open('w') as err:
            completed = subprocess.run(command, stdout=out, stderr=err, timeout=240)
        if completed.returncode:
            raise RuntimeError(action + ' failed; preserve evidence, do not retry')
    capture(root, 'host-after.txt', ['top', '-l', '2', '-s', '2', '-n', '8', '-o', 'cpu', '-stats', 'pid,command,cpu'])
    result = json.loads((root / 'evaluate-result.json').read_text())
    report = json.loads(Path(result['report_json']).read_text())
    print(json.dumps({'status': report['status'], 'parity': report['parity'],
          'workloads': [{'id': w['id'], 'metrics': w['metrics']} for w in report['workloads']]}, indent=2))
    print('Independent review required. No automatic memory attestation or adoption.')


if __name__ == '__main__':
    main()
