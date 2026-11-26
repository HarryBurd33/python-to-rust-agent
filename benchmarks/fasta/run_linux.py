#!/usr/bin/env python3
"""One frozen comparison after a bounded quiet gate on Ubuntu 24.04 ARM64."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

# Reuse snapshot identity checks and the already reviewed sanitized compiler proof.
spec = importlib.util.spec_from_file_location('_fasta_macos_wrapper', Path(__file__).with_name('run.py'))
frozen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frozen)
save = frozen.save
CONTRACT = frozen.CONTRACT


def pin_cpu(root):
    initial = sorted(os.sched_getaffinity(0))
    proof = {'initial_allowed_cpus': initial, 'selected_cpu': None, 'passed': False}
    save(root / 'affinity.json', proof)
    if not initial:
        raise RuntimeError('No initially allowed CPU')
    selected = max(initial)
    proof['selected_cpu'] = selected
    os.sched_setaffinity(0, {selected})
    proof['wrapper_allowed_cpus'] = sorted(os.sched_getaffinity(0))
    save(root / 'affinity.json', proof)
    if proof['wrapper_allowed_cpus'] != [selected]:
        raise RuntimeError('Wrapper CPU affinity did not match selected CPU')
    child = frozen.capture(root, 'affinity-child.json', [sys.executable, '-c',
                           'import json,os; print(json.dumps(sorted(os.sched_getaffinity(0))))'])
    proof['child_allowed_cpus'] = json.loads(child)
    proof['passed'] = proof['child_allowed_cpus'] == [selected]
    save(root / 'affinity.json', proof)
    if not proof['passed']:
        raise RuntimeError('Child did not inherit selected CPU affinity')
    return selected


def snapshot(root, name):
    # Preserve exact raw reads, including successful reads preceding a read error.
    raw = {'monotonic_seconds': time.monotonic(), 'errors': {}}
    for key, path in (('stat', '/proc/stat'), ('vmstat', '/proc/vmstat')):
        try:
            raw[key] = Path(path).read_text()
        except OSError as error:
            raw['errors'][key] = str(error)
    save(root / name, raw)
    return raw


def parse_snapshot(raw, selected):
    if raw.get('errors'):
        raise ValueError('Cannot read complete proc snapshot')
    cpus = {}
    for line in raw['stat'].splitlines():
        fields = line.split()
        if fields and fields[0] in ('cpu', f'cpu{selected}'):
            if fields[0] in cpus or len(fields) < 9:
                raise ValueError('Missing or duplicate CPU counters')
            # user,nice,system,idle,iowait,irq,softirq,steal. guest/guest_nice
            # are already included in user/nice and must not be added again.
            counters = [int(value) for value in fields[1:9]]
            if any(value < 0 for value in counters):
                raise ValueError('Negative CPU counter')
            cpus[fields[0]] = counters
    if set(cpus) != {'cpu', f'cpu{selected}'}:
        raise ValueError('Global or selected CPU row missing')
    swaps = {}
    for line in raw['vmstat'].splitlines():
        fields = line.split()
        if fields and fields[0] in ('pswpin', 'pswpout'):
            if fields[0] in swaps or len(fields) != 2:
                raise ValueError('Missing or duplicate swap counters')
            swaps[fields[0]] = int(fields[1])
    if set(swaps) != {'pswpin', 'pswpout'} or any(value < 0 for value in swaps.values()):
        raise ValueError('Missing or negative swap counters')
    return {'cpus': cpus, 'swaps': swaps}


def interval(before, after):
    result = {'passed': False, 'cpus': {}, 'swap_deltas': {}}
    for label, counters in before['cpus'].items():
        delta = [new - old for old, new in zip(counters, after['cpus'][label])]
        total = sum(delta)
        if any(value < 0 for value in delta) or total <= 0:
            raise ValueError('Regressing or zero-total CPU counters: ' + label)
        # Idle alone counts idle. In particular, iowait is treated as busy.
        idle = 100 * delta[3] / total
        result['cpus'][label] = {'deltas': delta, 'total': total,
                                 'idle_percent': idle, 'steal_delta': delta[7]}
    for label, value in before['swaps'].items():
        delta = after['swaps'][label] - value
        if delta < 0:
            raise ValueError('Regressing swap counter: ' + label)
        result['swap_deltas'][label] = delta
    result['passed'] = (all(row['idle_percent'] >= 85 and row['steal_delta'] == 0
                            for row in result['cpus'].values())
                        and all(value == 0 for value in result['swap_deltas'].values()))
    return result


def check_readiness(root, selected):
    readiness = {'passed': False, 'enforced': True, 'maximum_windows': 6,
                 'intervals_per_window': 5, 'interval_seconds': 2, 'gap_seconds': 20,
                 'selected_cpu': selected, 'qualified_window': None, 'windows': []}
    save(root / 'readiness.json', readiness)
    for number in range(1, 7):
        prefix = f'readiness-window-{number:02d}'
        rows = []
        for sample in range(6):
            if sample:
                time.sleep(2)
            raw = snapshot(root, f'{prefix}-sample-{sample:02d}.json')
            try:
                rows.append(parse_snapshot(raw, selected))
            except (ValueError, KeyError, TypeError) as error:
                rows.append({'parse_error': str(error)})
        window = {'window': number, 'passed': False, 'intervals': [], 'errors': []}
        for index in range(5):
            try:
                if 'parse_error' in rows[index] or 'parse_error' in rows[index + 1]:
                    raise ValueError('Invalid adjacent snapshot: ' + repr(rows[index:index + 2]))
                window['intervals'].append(interval(rows[index], rows[index + 1]))
            except (ValueError, KeyError, TypeError) as error:
                window['errors'].append({'interval': index + 1, 'error': str(error)})
        window['passed'] = (not window['errors'] and len(window['intervals']) == 5
                            and all(row['passed'] for row in window['intervals']))
        save(root / (prefix + '.json'), window)
        readiness['windows'].append(window)
        if window['passed']:
            readiness.update(passed=True, qualified_window=number)
        save(root / 'readiness.json', readiness)
        if readiness['passed']:
            return readiness
        if number < 6:
            time.sleep(20)
    raise RuntimeError('Linux VM did not meet readiness; no performance run started')


def qualify_rss(root):
    spec = importlib.util.spec_from_file_location('_rss_builder', Path(__file__).with_name('rss_build.py'))
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    derivative = builder.build(root)
    # All fixed tests and qualification diagnostics precede readiness and FASTA.
    try:
        result = subprocess.run([sys.executable, str(root / 'test_rss.py'), '--root', str(root)],
                                capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as error:
        def decoded(value):
            return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
        (root / 'rss-tests.log').write_text(decoded(error.stdout) + decoded(error.stderr))
        raise RuntimeError('RSS adapter tests timed out; no FASTA comparison started') from error
    (root / 'rss-tests.log').write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError('RSS adapter regression tests failed; no FASTA comparison started')
    spec = importlib.util.spec_from_file_location('_rss_diagnostics', root / 'rss_diagnostics.py')
    diagnostics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diagnostics)
    diagnostics.qualify(root)
    return derivative


def host_info(root):
    raw = {'platform': platform.platform(), 'machine': platform.machine(), 'errors': {}}
    for label, path in (('cpuinfo', '/proc/cpuinfo'), ('meminfo', '/proc/meminfo'),
                        ('version', '/proc/version'), ('os_release', '/etc/os-release')):
        try:
            raw[label] = Path(path).read_text()
        except OSError as error:
            raw['errors'][label] = str(error)
    save(root / 'linux-host.json', raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    root = args.destination.resolve()
    package = frozen.restore(root)
    save(root / 'snapshot-manifest.json', {k: {'sha256': v['sha256'], 'size': v['size']} for k, v in package['files'].items()})
    if args.verify_only:
        print('Verified exact bundle:', len(package['files']), 'files; no benchmark ran')
        return
    if platform.system() != 'Linux' or platform.machine() != 'aarch64':
        raise RuntimeError('Requires the predeclared Linux aarch64 runner')
    if sys.version_info[:3] != (3, 15, 0):
        raise RuntimeError('Requires Python 3.15.0')
    selected = pin_cpu(root)
    frozen.compiler_preflight(root)
    frozen.capture(root, 'python-version.txt', [sys.executable, '--version'])
    save(root / 'runner.json', {k: os.environ.get(k) for k in ('RUNNER_OS', 'RUNNER_ARCH', 'ImageOS', 'ImageVersion', 'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')})
    host_info(root)
    derivative = qualify_rss(root)
    check_readiness(root, selected)
    affinity = sorted(os.sched_getaffinity(0))
    save(root / 'affinity-postreadiness.json', {'selected_cpu': selected, 'allowed_cpus': affinity,
                                               'passed': affinity == [selected]})
    if affinity != [selected]:
        raise RuntimeError('CPU affinity changed before measurement')
    try:
        common = [sys.executable, str(derivative)]
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
    finally:
        snapshot(root, 'host-after.json')
        save(root / 'affinity-after.json', {'allowed_cpus': sorted(os.sched_getaffinity(0))})
    result = json.loads((root / 'evaluate-result.json').read_text())
    report = json.loads(Path(result['report_json']).read_text())
    print(json.dumps({'status': report['status'], 'parity': report['parity'],
          'workloads': [{'id': w['id'], 'metrics': w['metrics']} for w in report['workloads']]}, indent=2))
    print('Linux-specific comparison. Independent review required; no automatic memory attestation or adoption.')


if __name__ == '__main__':
    main()
