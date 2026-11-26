"""Same bounded readiness, requiring global plus both selected CPUs."""
import importlib.util
from pathlib import Path
import time
_spec=importlib.util.spec_from_file_location('_placement_legacy_linux',Path(__file__).with_name('run_linux.py'))
legacy=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(legacy)
save=legacy.save


def parse_snapshot(raw,config):
    if config['host_cpu']==config['target_cpu']:raise ValueError('Need two distinct CPUs')
    host=legacy.parse_snapshot(raw,config['host_cpu'])
    target=legacy.parse_snapshot(raw,config['target_cpu'])
    return {'cpus':dict(host['cpus'],**target['cpus']),'swaps':host['swaps']}


def check_readiness(root, config):
    readiness = {'passed': False, 'enforced': True, 'maximum_windows': 6,
                 'intervals_per_window': 5, 'interval_seconds': 2, 'gap_seconds': 20,
                 'host_cpu': config['host_cpu'], 'target_cpu': config['target_cpu'], 'qualified_window': None, 'windows': []}
    save(root / 'readiness.json', readiness)
    for number in range(1, 7):
        prefix = f'readiness-window-{number:02d}'
        rows = []
        for sample in range(6):
            if sample:
                time.sleep(2)
            raw = legacy.snapshot(root, f'{prefix}-sample-{sample:02d}.json')
            try:
                rows.append(parse_snapshot(raw, config))
            except (ValueError, KeyError, TypeError) as error:
                rows.append({'parse_error': str(error)})
        window = {'window': number, 'passed': False, 'intervals': [], 'errors': []}
        for index in range(5):
            try:
                if 'parse_error' in rows[index] or 'parse_error' in rows[index + 1]:
                    raise ValueError('Invalid adjacent snapshot: ' + repr(rows[index:index + 2]))
                window['intervals'].append(legacy.interval(rows[index], rows[index + 1]))
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

