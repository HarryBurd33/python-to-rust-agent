"""Exactly 64 counterbalanced non-FASTA controls, never performance-selected."""
import importlib.util
import json
import math
from pathlib import Path
import statistics
import time


def load(path, name):
    spec = importlib.util.spec_from_file_location(name,path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def schedule():
    rows = []
    for block in range(16):
        durations = (0,1) if block % 2 == 0 else (1,0)
        for duration in durations:
            modes = ('polling','pidfd') if (block//2+duration)%2 == 0 else ('pidfd','polling')
            for order,mode in enumerate(modes):
                rows.append({'block':block,'duration_index':duration,'dwell_us':(0,3500)[duration],
                             'mode':mode,'order_in_pair':order,'duration_position':durations.index(duration)})
    return rows


def validate(row):
    observation, output = row['observation'], row['output']
    if observation['timing_mode'] != row['mode']:
        raise ValueError('Control method label mismatch')
    if (observation['exit_code'] != 0 or observation['timeout'] or observation['residual_process_group']
            or observation['instrument_error'] or not observation['rss_receipt']):
        raise ValueError('Control execution/protocol failed')
    if set(output) != {'dwell_us','entry_monotonic_ns','finish_monotonic_ns'} or any(type(v) is not int for v in output.values()):
        raise ValueError('Invalid native timestamp output')
    if output['dwell_us'] != row['dwell_us']:
        raise ValueError('Control dwell mismatch')
    start, popen, end = (observation[key] for key in ('started_monotonic','popen_return_monotonic','finished_monotonic'))
    entry, finish = output['entry_monotonic_ns']/1e9, output['finish_monotonic_ns']/1e9
    if (not all(math.isfinite(value) for value in (start,popen,end,entry,finish))
            or not start <= popen <= end or not start <= entry <= finish <= end
            or observation['elapsed_seconds'] != end-start):
        raise ValueError('Invalid control phase timestamps')
    if end > row['deadline_monotonic']:
        raise ValueError('Control exceeded its launch deadline')
    if output['finish_monotonic_ns']-output['entry_monotonic_ns'] < row['dwell_us']*1000:
        raise ValueError('Absolute dwell completed too soon')
    row['segments_seconds'] = {'elapsed':end-start,'start_to_entry':entry-start,
                              'target_body':finish-entry,'finish_to_end':end-finish}


def summarize(rows):
    summary = {'groups':[],'paired_differences':[]}
    for dwell in (0,3500):
        for mode in ('polling','pidfd'):
            group = [row for row in rows if row['dwell_us']==dwell and row['mode']==mode]
            metrics = {}
            for segment in ('elapsed','start_to_entry','target_body','finish_to_end'):
                values = [row['segments_seconds'][segment] for row in group]
                median = statistics.median(values); width = max(values)-min(values)
                metrics[segment] = {'raw':values,'median':median,'range':width,'range_over_median':width/median if median else None}
            summary['groups'].append({'dwell_us':dwell,'mode':mode,'metrics':metrics})
        differences = {}
        for segment in ('elapsed','finish_to_end'):
            values = []
            for block in range(16):
                pair = {row['mode']:row for row in rows if row['block']==block and row['dwell_us']==dwell}
                values.append(pair['polling']['segments_seconds'][segment]-pair['pidfd']['segments_seconds'][segment])
            differences[segment] = {'raw_poll_minus_pidfd':values,'median':statistics.median(values)}
        summary['paired_differences'].append({'dwell_us':dwell,'metrics':differences})
    return summary


def run(root):
    methods = {mode:load(root/filename,'_control_'+mode) for mode,filename in
               (('polling','optimize-linux-timing-polling.py'),('pidfd','optimize-linux-pidfd.py'))}
    work = root/'timing-control-work'; work.mkdir()
    result = {'production_mode':'pidfd','performance_selection':False,'planned_calls':64,'passed':False,'records':[]}
    save = methods['pidfd'].save
    started = time.monotonic(); stop = started+60
    result.update(series_started_monotonic=started,series_deadline_monotonic=stop)
    save(root/'timing-controls.json',result)
    for ordinal, planned in enumerate(schedule()):
        row = dict(planned,ordinal=ordinal)
        try:
            before = time.monotonic()
            if before >= stop:
                raise RuntimeError('Timing control series exhausted 60-second bound')
            deadline = min(stop,before+3)
            row['deadline_monotonic'] = deadline
            module = methods[row['mode']]
            row['observation'] = module.measure([str(root/'timing-control'),str(row['dwell_us'])],work,
                module.runtime_environment(),deadline,capture_version=True,record_effects=False)
            row['output'] = json.loads(row['observation']['version_output'])
            validate(row)
            if time.monotonic() > stop:
                raise RuntimeError('Timing controls exceeded total deadline')
        except Exception as error:
            row['functional_error'] = repr(error)
            result['records'].append(row)
            result['series_finished_monotonic'] = time.monotonic()
            save(root/f'timing-control-{ordinal:02d}.json',row)
            save(root/'timing-controls.json',result)
            raise RuntimeError('Timing control functional failure; no FASTA comparison started') from error
        result['records'].append(row)
        save(root/f'timing-control-{ordinal:02d}.json',row)
        save(root/'timing-controls.json',result)
    result['series_finished_monotonic'] = time.monotonic()
    result['summary'] = summarize(result['records'])
    result['passed'] = len(result['records'])==64 and result['series_finished_monotonic']<=stop
    save(root/'timing-controls.json',result)
    if not result['passed']:
        raise RuntimeError('Timing control series incomplete; no FASTA comparison started')
    return result
