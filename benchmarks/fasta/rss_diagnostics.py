"""Fixed qualification only; no FASTA workload or measurement retry."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

MIB = 1024 * 1024


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def qualify(root):
    module = load(root / 'optimize-linux-rss.py', '_rss_qualified_evaluator')
    original = load(root / 'optimize.py', '_rss_direct_control')
    environment = module.runtime_environment()
    work = root / 'rss-diagnostic-work'
    work.mkdir()
    result = {'passed': False, 'native': [], 'python': [], 'errors': [], 'checks': {}}
    def retain():
        module.save(root / 'rss-qualification.json', result)
    def measure(evaluator, argv):
        return evaluator.measure(argv, work, environment, time.monotonic() + 20,
                                 capture_version=True, record_effects=False)
    def qualified(row):
        return (row.get('exit_code') == 0 and not row.get('timeout')
                and not row.get('residual_process_group') and row.get('peak_rss_bytes') is not None
                and row.get('rss_receipt') and not row['rss_receipt']['floor_limited']
                and row['peak_rss_bytes'] > row['rss_receipt']['preexec_ceiling_bytes'])
    pressure = None
    for state in ('ordinary', 'plus64mib'):
        if state == 'plus64mib':
            pressure = bytearray(64 * MIB)
            for offset in range(0, len(pressure), 4096):
                pressure[offset] = 1
        parent = {'state': state, 'resident_bytes': int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')}
        result.setdefault('parent', []).append(parent)
        for instrument, evaluator in (('historical_direct', original), ('repaired', module)):
            item = {'state': state, 'instrument': instrument}
            try:
                item['observation'] = measure(evaluator, [str(root / 'rss-fixture'), 'touch'])
                item['output'] = json.loads(item['observation']['version_output'])
            except Exception as error:
                item['error'] = repr(error)
                result['errors'].append('native ' + state + '/' + instrument + ': ' + repr(error))
            result['native'].append(item)
            retain()
    # Keep the inflated allocation live through both measurements above.
    if pressure is not None:
        result['pressure_checksum'] = sum(pressure[::4096])
    del pressure
    for size in (0, 64, 128):
        item = {'allocated_mib': size}
        try:
            item['observation'] = measure(module, [sys.executable, str(root / 'memory-probe.py'), str(size)])
            item['output'] = json.loads(item['observation']['version_output'])
            item['child_self_ru_maxrss_unit'] = 'KiB'
            item['child_self_ru_maxrss_bytes'] = item['output']['self_ru_maxrss'] * 1024
        except Exception as error:
            item['error'] = repr(error)
            result['errors'].append('python ' + str(size) + ': ' + repr(error))
        result['python'].append(item)
        retain()
    try:
        native = [item for item in result['native'] if item['instrument'] == 'repaired']
        result['checks']['pressure_live'] = (result['pressure_checksum'] == 64*MIB//4096
            and result['parent'][1]['resident_bytes'] - result['parent'][0]['resident_bytes'] >= 60*MIB)
        result['checks']['native_outputs'] = all(item['output'] == {'touch_count': 512, 'allocated_bytes': 2*MIB} for item in result['native'])
        result['checks']['native_qualified'] = all(qualified(item['observation']) and item['observation']['peak_rss_bytes'] < 16*MIB for item in native)
        result['checks']['native_pressure_invariant'] = abs(native[0]['observation']['peak_rss_bytes'] - native[1]['observation']['peak_rss_bytes']) <= 2*MIB
        result['checks']['python_qualified'] = all(qualified(item['observation']) for item in result['python'])
        result['checks']['python_outputs'] = all(
            item['output']['allocated_bytes'] == item['allocated_mib']*MIB
            and item['output']['checksum'] == item['allocated_mib']*MIB//4096
            and type(item['output']['self_ru_maxrss']) is int and item['output']['self_ru_maxrss'] > 0
            and item['observation']['wait4_ru_maxrss_unit'] == 'KiB'
            and item['observation']['peak_rss_bytes'] == item['observation']['wait4_ru_maxrss']*1024
            for item in result['python'])
        peaks = [item['observation']['peak_rss_bytes'] for item in result['python']]
        increments = []
        for start, stop, expected in ((0, 1, 64*MIB), (0, 2, 128*MIB), (1, 2, 64*MIB)):
            actual = peaks[stop] - peaks[start]
            tolerance = max(2*MIB, expected*0.05)
            increments.append({'from_mib': result['python'][start]['allocated_mib'],
                               'to_mib': result['python'][stop]['allocated_mib'], 'expected_bytes': expected,
                               'actual_bytes': actual, 'tolerance_bytes': tolerance, 'passed': abs(actual - expected) <= tolerance})
        result['increments'] = increments
        result['checks']['python_increments'] = all(item['passed'] for item in increments)
    except Exception as error:
        result['errors'].append('qualification validation: ' + repr(error))
    result['passed'] = not result['errors'] and len(result['checks']) == 7 and all(result['checks'].values())
    retain()
    if not result['passed']:
        raise RuntimeError('RSS instrument qualification failed; no FASTA comparison started')
    return result
