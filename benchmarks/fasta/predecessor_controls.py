"""Fixed busy/sleeping predecessor pairs; no production gap or performance selection."""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import time


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def schedule():
    return [{'block':block,'pair_order':order,'mode':mode} for block in range(16)
            for order,mode in enumerate(('busy','sleeping') if block%2==0 else ('sleeping','busy'))]


def cpu_us(value):
    return (value['user_sec']+value['system_sec'])*1000000+value['user_usec']+value['system_usec']


def validate_call(row,config,root,accounting,placement):
    observed=row['observation'];output=json.loads(observed['version_output']);row['output']=output
    expected=[str(root/'predecessor-control'),row['mode']] if row['role']=='predecessor' else [str(root/'placement-control'),'0']
    if (observed['argv']!=expected or observed['placement_mode']!='split' or observed['timing_mode']!='pidfd'
        or observed['exit_code']!=0 or observed['timeout'] or observed['residual_process_group'] or observed['instrument_error']):
        raise ValueError('Diagnostic execution/argv/mode failed')
    if row['parent_before']!=[config['host_cpu']] or row['parent_after']!=[config['host_cpu']]:raise ValueError('Parent left CPU H')
    raw=(observed['version_output']+'\n').encode()
    if observed['stdout']!={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} or observed['stderr']!={'size':0,'sha256':hashlib.sha256(b'').hexdigest()}:
        raise ValueError('Diagnostic stdout/stderr signature mismatch')
    account=observed['accounting_receipt']
    if account is None:raise ValueError('Missing target accounting')
    if (any(type(output.get(key)) is not int or not 0<=output[key]<(1<<63) for key in ('entry_wall_ns','finish_wall_ns'))
        or not account['fork_monotonic_ns']<=output['entry_wall_ns']<=output['finish_wall_ns']<=account['reap_monotonic_ns']):
        raise ValueError('Target body lies outside native fork/reap marks')
    phases=accounting.phases(observed['started_monotonic'],observed['pre_popen_monotonic'],observed['popen_return_monotonic'],observed['finished_monotonic'],account)
    if observed['exclusive_phases_seconds']!=phases or observed['elapsed_seconds']!=observed['finished_monotonic']-observed['started_monotonic']:
        raise ValueError('Duration/phase mismatch')
    if observed['finished_monotonic']>row['deadline_monotonic']:raise ValueError('Call exceeded deadline')
    if row['role']=='probe':
        original={'mode':'split','budget_us':0,'parent_before':row['parent_before'],'parent_after':row['parent_after'],
                  'deadline_monotonic':row['deadline_monotonic'],'observation':observed,'output':output}
        placement.validate(original,config)
        row['target_cpu_seconds']=cpu_us(account)/1e6
        return
    keys={'mode','entry_wall_ns','finish_wall_ns','entry_cpu_ns','finish_cpu_ns','target_pid','cpu_count','cpu','current_cpu','self_rusage'}
    if set(output)!=keys or output['mode']!=row['mode'] or any(type(output[key]) is not int for key in keys-{'mode','self_rusage'}):
        raise ValueError('Invalid predecessor output')
    if (output['target_pid']!=observed['rss_receipt']['target_pid'] or output['cpu_count']!=1 or output['cpu']!=config['target_cpu']
        or output['current_cpu']!=config['target_cpu']):raise ValueError('Predecessor target placement/PID mismatch')
    entry=output['entry_wall_ns']/1e9;finish=output['finish_wall_ns']/1e9
    if not observed['started_monotonic']<=entry<=finish<=observed['finished_monotonic'] or output['finish_wall_ns']-output['entry_wall_ns']<250000000:
        raise ValueError('Predecessor wall ordering or 250 ms duration failed')
    body_cpu=output['finish_cpu_ns']-output['entry_cpu_ns']
    if output['entry_cpu_ns']<0 or body_cpu<0:raise ValueError('Predecessor thread CPU regressed')
    own=output['self_rusage'];usage_keys={'user_sec','user_usec','system_sec','system_usec'}|set(accounting.COUNTERS)
    if (not isinstance(own,dict) or set(own)!=usage_keys or any(type(v) is not int or not 0<=v<(1<<63) for v in own.values())
        or own['user_usec']>=1000000 or own['system_usec']>=1000000):raise ValueError('Invalid self rusage units/fields')
    waited=cpu_us(account);snapshot=cpu_us(own)
    if waited+2<snapshot or any(account[key]<own[key] for key in accounting.COUNTERS):raise ValueError('Waited rusage does not cover self snapshot')
    if waited*1000+10000000<body_cpu or waited*1000>account['reap_monotonic_ns']-account['fork_monotonic_ns']+10000000:
        raise ValueError('Target CPU attribution/time units failed')
    row['body_wall_seconds']=(output['finish_wall_ns']-output['entry_wall_ns'])/1e9
    row['body_cpu_seconds']=body_cpu/1e9
    row['target_cpu_seconds']=waited/1e6


def validate_gap(pair):
    gap=pair['probe']['observation']['started_monotonic']-pair['predecessor']['observation']['finished_monotonic']
    pair['actual_gap_seconds']=gap
    if not math.isfinite(gap) or gap<0.002:raise ValueError('Diagnostic paired gap shorter than 2 ms')


def summarize(pairs):
    phases=('stream_preparation','launch_to_fork','fork_to_reap','report_to_end')
    def values(pair):
        probe=pair['probe'];pred=pair['predecessor'];observed=probe['observation']
        return {'elapsed_seconds':observed['elapsed_seconds'],**observed['exclusive_phases_seconds'],
            'target_cpu_seconds':probe['target_cpu_seconds'],'popen_offset_seconds':observed['popen_return_monotonic']-observed['started_monotonic'],
            'gap_seconds':pair['actual_gap_seconds'],'predecessor_wall_seconds':pred['body_wall_seconds'],'predecessor_cpu_seconds':pred['body_cpu_seconds']}
    def stats(raw):
        median=statistics.median(raw);width=max(raw)-min(raw)
        return {'raw':raw,'median':median,'range':width,'range_over_median':width/median if median else None}
    result={'groups':[],'within_block_busy_minus_sleeping':{}}
    for mode in ('busy','sleeping'):
        group=[pair for pair in pairs if pair['mode']==mode];metrics={}
        for key in values(group[0]):metrics[key]=stats([values(pair)[key] for pair in group])
        resources={key:stats([pair['probe']['observation']['accounting_receipt'][key] for pair in group]) for key in
            ('minor_faults','major_faults','block_inputs','block_outputs','voluntary_switches','involuntary_switches')}
        result['groups'].append({'mode':mode,'metrics':metrics,'probe_resource_counts':resources})
    for key in ('elapsed_seconds',)+phases+('target_cpu_seconds',):
        raw=[]
        for block in range(16):
            pair={row['mode']:row for row in pairs if row['block']==block}
            raw.append(values(pair['busy'])[key]-values(pair['sleeping'])[key])
        result['within_block_busy_minus_sleeping'][key]={'raw':raw,'median':statistics.median(raw),'signs':[(v>0)-(v<0) for v in raw]}
    return result


def run(root,config):
    module=load(root/'optimize-linux-accounting.py','_predecessor_production')
    accounting=load(root/'accounting_adapter.py','_diagnostic_accounting')
    placement=load(root/'placement_controls.py','_unchanged_probe_validation')
    affinity=load(root/'placement_config.py','_unchanged_affinity')
    work=root/'predecessor-work';work.mkdir()
    result={'planned_calls':64,'production_method':'split/pidfd/accounting','performance_selection':False,'passed':False,'pairs':[]}
    start=time.monotonic();stop=start+60;result.update(series_started_monotonic=start,series_deadline_monotonic=stop)
    module.save(root/'predecessor-controls.json',result)
    def persist(pair,ordinal):
        module.save(root/f'predecessor-pair-{ordinal:02d}.json',pair);module.save(root/'predecessor-controls.json',result)
    try:
        for ordinal,plan in enumerate(schedule()):
            pair=dict(plan,ordinal=ordinal)
            try:
                for role in ('predecessor','probe'):
                    if role=='probe':
                        due=math.nextafter(pair['predecessor']['observation']['finished_monotonic']+0.002,math.inf)
                        while True:
                            now=time.monotonic()
                            if now>=stop:raise RuntimeError('Series deadline before probe gap completed')
                            if now>=due:break
                            time.sleep(min(due-now,stop-now))
                    row={'role':role,'mode':plan['mode']};pair[role]=row
                    before=time.monotonic()
                    if before>=stop:raise RuntimeError('Diagnostic series deadline exhausted')
                    row['deadline_monotonic']=min(stop,before+3)
                    row['parent_before']=affinity.require_parent(config['host_cpu'])
                    argv=[str(root/'predecessor-control'),plan['mode']] if role=='predecessor' else [str(root/'placement-control'),'0']
                    try:
                        row['observation']=module.measure(argv,work,module.runtime_environment(),row['deadline_monotonic'],capture_version=True,record_effects=False)
                    finally:row['parent_after']=sorted(os.sched_getaffinity(0))
                    validate_call(row,config,root,accounting,placement)
                    if time.monotonic()>stop:raise RuntimeError('Diagnostic series exceeded 60 seconds')
                validate_gap(pair)
            except BaseException as error:
                pair['functional_error']=repr(error);result['pairs'].append(pair);persist(pair,ordinal)
                if not isinstance(error,Exception):raise
                raise RuntimeError('Predecessor diagnostic functional failure; no FASTA comparison started') from error
            # No aggregate write occurs between predecessor and probe.
            result['pairs'].append(pair);persist(pair,ordinal)
    finally:
        result['series_finished_monotonic']=time.monotonic();result['parent_after']=sorted(os.sched_getaffinity(0))
        module.save(root/'predecessor-controls.json',result)
    result['completed_calls']=sum('observation' in pair.get(role,{}) for pair in result['pairs'] for role in ('predecessor','probe'))
    result['summary']=summarize(result['pairs'])
    result['passed']=len(result['pairs'])==32 and result['completed_calls']==64 and result['series_finished_monotonic']<=stop and result['parent_after']==[config['host_cpu']]
    module.save(root/'predecessor-controls.json',result)
    if not result['passed']:raise RuntimeError('Incomplete predecessor series; no FASTA comparison started')
    return result
