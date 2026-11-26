"""Fixed 64 runnable controls; same shim/target/pidfd, parent placement alone varies."""
import importlib.util
import json
import hashlib
import math
from pathlib import Path
import os
import statistics
import time


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def schedule():
    rows=[]
    for block in range(16):
        durations=(0,1) if block%2==0 else (1,0)
        for duration in durations:
            modes=('shared','split') if (block//2+duration)%2==0 else ('split','shared')
            for order,mode in enumerate(modes):
                rows.append({'block':block,'duration_index':duration,'budget_us':(0,500)[duration],
                    'mode':mode,'order_in_pair':order,'duration_position':durations.index(duration)})
    return rows


def validate(row,config):
    observed,output=row['observation'],row['output']
    if observed['placement_mode']!=row['mode'] or observed['timing_mode']!='pidfd':raise ValueError('Wrong control mode')
    if (observed['exit_code']!=0 or observed['timeout'] or observed['residual_process_group'] or observed['instrument_error']
        or not observed['rss_receipt'] or not observed['placement_receipt']):raise ValueError('Control execution/protocol failed')
    keys={'budget_us','entry_wall_ns','finish_wall_ns','entry_cpu_ns','finish_cpu_ns','target_pid','cpu_count','cpu','current_cpu'}
    if set(output)!=keys or any(type(value) is not int for value in output.values()):raise ValueError('Invalid native output')
    if (output['budget_us']!=row['budget_us'] or output['target_pid']!=observed['rss_receipt']['target_pid']
        or output['cpu_count']!=1 or output['cpu']!=config['target_cpu'] or output['current_cpu']!=config['target_cpu']
        or observed['placement_receipt']['cpu']!=config['target_cpu']):raise ValueError('Target placement/PID mismatch')
    raw=(observed['version_output']+'\n').encode('utf-8')
    signature={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    empty={'size':0,'sha256':hashlib.sha256(b'').hexdigest()}
    if observed['stdout']!=signature or observed['stderr']!=empty or json.loads(observed['version_output'])!=output:
        raise ValueError('Control raw stdout/stderr or parsed output mismatch')
    expected=config['target_cpu'] if row['mode']=='shared' else config['host_cpu']
    if row['parent_before']!=[expected] or row['parent_after']!=[expected]:raise ValueError('Parent placement mismatch')
    start,popen,end=(observed[key] for key in ('started_monotonic','popen_return_monotonic','finished_monotonic'))
    entry,finish=output['entry_wall_ns']/1e9,output['finish_wall_ns']/1e9
    if (not all(math.isfinite(value) for value in (start,popen,end,entry,finish)) or not start<=popen<=end
        or not start<=entry<=finish<=end or observed['elapsed_seconds']!=end-start or end>row['deadline_monotonic']):
        raise ValueError('Invalid wall endpoints/deadline')
    cpu=output['finish_cpu_ns']-output['entry_cpu_ns']
    if output['entry_cpu_ns']<0 or cpu<row['budget_us']*1000:raise ValueError('CPU budget incomplete/regressing')
    row['segments_seconds']={'elapsed':end-start,'start_to_popen':popen-start,'popen_to_end':end-popen,
        'start_to_entry':entry-start,'body_wall':finish-entry,'body_cpu':cpu/1e9,'finish_to_end':end-finish}
    row['entry_before_popen']=entry<popen;row['finish_before_popen']=finish<popen


def summarize(rows):
    result={'groups':[],'paired_differences':[]}
    segments=('elapsed','start_to_popen','popen_to_end','start_to_entry','body_wall','body_cpu','finish_to_end')
    for budget in (0,500):
        for mode in ('shared','split'):
            group=[row for row in rows if row['budget_us']==budget and row['mode']==mode]
            metrics={}
            for segment in segments:
                values=[row['segments_seconds'][segment] for row in group];median=statistics.median(values);width=max(values)-min(values)
                metrics[segment]={'raw':values,'median':median,'range':width,'range_over_median':width/median if median else None}
            result['groups'].append({'budget_us':budget,'mode':mode,'metrics':metrics,
                'entry_before_popen_count':sum(row['entry_before_popen'] for row in group),
                'finish_before_popen_count':sum(row['finish_before_popen'] for row in group)})
        differences={}
        for segment in ('elapsed','start_to_popen','popen_to_end'):
            values=[]
            for block in range(16):
                pair={row['mode']:row for row in rows if row['block']==block and row['budget_us']==budget}
                values.append(pair['shared']['segments_seconds'][segment]-pair['split']['segments_seconds'][segment])
            differences[segment]={'raw_shared_minus_split':values,'median':statistics.median(values)}
        result['paired_differences'].append({'budget_us':budget,'metrics':differences})
    return result


def run(root,config):
    module=load(root/'optimize-linux-split.py','_placement_control_production')
    affinity=load(root/'placement_config.py','_control_affinity')
    work=root/'placement-control-work';work.mkdir()
    result={'production_placement':'split','performance_selection':False,'planned_calls':64,'passed':False,'records':[]}
    started=time.monotonic();stop=started+60
    result.update(series_started_monotonic=started,series_deadline_monotonic=stop)
    module.save(root/'placement-controls.json',result)
    try:
        for ordinal,plan in enumerate(schedule()):
            row=dict(plan,ordinal=ordinal)
            try:
                before=time.monotonic()
                if before>=stop:raise RuntimeError('Control series deadline exhausted')
                expected=config['target_cpu'] if row['mode']=='shared' else config['host_cpu']
                row['parent_before']=affinity.set_parent(expected)
                row['deadline_monotonic']=min(stop,before+3)
                module._PLACEMENT_MODE=row['mode']
                try:
                    row['observation']=module.measure([str(root/'placement-control'),str(row['budget_us'])],work,
                        module.runtime_environment(),row['deadline_monotonic'],capture_version=True,record_effects=False)
                finally:
                    row['parent_after']=sorted(os.sched_getaffinity(0))
                row['output']=json.loads(row['observation']['version_output'])
                validate(row,config)
                if time.monotonic()>stop:raise RuntimeError('Control series exceeded 60 seconds')
            except Exception as error:
                row['functional_error']=repr(error);result['records'].append(row)
                module.save(root/f'placement-control-{ordinal:02d}.json',row)
                module.save(root/'placement-controls.json',result)
                raise RuntimeError('Placement control functional failure; no FASTA comparison started') from error
            result['records'].append(row)
            module.save(root/f'placement-control-{ordinal:02d}.json',row)
            module.save(root/'placement-controls.json',result)
    finally:
        module._PLACEMENT_MODE='split'
        try:
            result['restored_parent']=affinity.set_parent(config['host_cpu'])
        except Exception as error:
            result['restore_error']=repr(error)
            raise RuntimeError('Could not restore evaluator CPU H') from error
        finally:
            result['series_finished_monotonic']=time.monotonic()
            module.save(root/'placement-controls.json',result)
    result['summary']=summarize(result['records'])
    result['passed']=len(result['records'])==64 and result['series_finished_monotonic']<=stop
    module.save(root/'placement-controls.json',result)
    if not result['passed']:raise RuntimeError('Incomplete placement controls; no FASTA comparison started')
    return result
