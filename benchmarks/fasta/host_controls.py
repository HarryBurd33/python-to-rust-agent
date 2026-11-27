"""Fixed H/T by activity controls; supervisor T and body placement remain distinct."""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time

CONDITIONS=(('busy','host_cpu'),('sleeping','host_cpu'),('busy','target_cpu'),('sleeping','target_cpu'))


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def schedule():
    return [{'block':block,'position':position,'condition':condition,'condition_code':chr(65+condition),
             'mode':CONDITIONS[condition][0],'placement_role':CONDITIONS[condition][1],'budget_us':250000}
            for block in range(8) for position,condition in enumerate([(value+block)%4 for value in (0,1,3,2)])]


def argv(root,row,config):
    if row['role']=='probe':return [str(root/'dynamic-probe'),'0']
    return [row['taskset_path'],'--cpu-list',str(config[row['placement_role']]),str(root/'crossover-predecessor'),row['mode'],'250000']



def cpu_us(value):return (value['user_sec']+value['system_sec'])*1000000+value['user_usec']+value['system_usec']


def validate_call(row,config,root,accounting,placement):
    observed=row['observation'];output=json.loads(observed['version_output']);row['output']=output
    expected=argv(root,row,config)
    if (observed['argv']!=expected or observed['placement_mode']!='split' or observed['timing_mode']!='pidfd'
        or observed['exit_code']!=0 or observed['timeout'] or observed['residual_process_group'] or observed['instrument_error']):
        raise ValueError('Diagnostic execution/argv/mode failed')
    supervisor=observed['placement_receipt']
    if not supervisor or supervisor.get('cpu')!=config['target_cpu'] or supervisor.get('cpu_count')!=1 or supervisor.get('supervisor_pid')!=observed['supervisor_pid']:
        raise ValueError('Supervisor must independently prove CPU T')
    if row['parent_before']!=[config['host_cpu']] or row['parent_after']!=[config['host_cpu']]:raise ValueError('Parent left CPU H')
    raw=(observed['version_output']+'\n').encode()
    if observed['stdout']!={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} or observed['stderr']!={'size':0,'sha256':hashlib.sha256(b'').hexdigest()}:
        raise ValueError('Diagnostic stdout/stderr signature mismatch')
    account=observed['accounting_receipt']
    if account is None:raise ValueError('Missing target accounting')
    phases=accounting.phases(observed['started_monotonic'],observed['pre_popen_monotonic'],observed['popen_return_monotonic'],observed['finished_monotonic'],account)
    if observed['exclusive_phases_seconds']!=phases or observed['elapsed_seconds']!=observed['finished_monotonic']-observed['started_monotonic']:
        raise ValueError('Duration/phase mismatch')
    if observed['finished_monotonic']>row['deadline_monotonic']:raise ValueError('Call exceeded deadline')
    if (any(type(output.get(key)) is not int or not 0<=output[key]<(1<<63) for key in ('entry_wall_ns','finish_wall_ns'))
        or not account['fork_monotonic_ns']<=output['entry_wall_ns']<=output['finish_wall_ns']<=account['reap_monotonic_ns']):
        raise ValueError('Target body lies outside native fork/reap marks')
    if row['role']=='probe':
        original={'mode':'split','budget_us':0,'parent_before':row['parent_before'],'parent_after':row['parent_after'],
                  'deadline_monotonic':row['deadline_monotonic'],'observation':observed,'output':output}
        placement.validate(original,config)
        row['target_cpu_seconds']=cpu_us(account)/1e6
        return
    keys={'mode','entry_wall_ns','finish_wall_ns','entry_cpu_ns','finish_cpu_ns','target_pid','cpu_count','cpu','current_cpu','self_rusage','budget_us'}
    if set(output)!=keys or output['mode']!=row['mode'] or output['budget_us']!=row['budget_us'] or any(type(output[key]) is not int for key in keys-{'mode','self_rusage','budget_us'}):
        raise ValueError('Invalid predecessor output')
    if (output['target_pid']!=observed['rss_receipt']['target_pid'] or output['cpu_count']!=1 or output['cpu']!=config[row['placement_role']]
        or output['current_cpu']!=config[row['placement_role']]):raise ValueError('Predecessor target placement/PID mismatch')
    entry=output['entry_wall_ns']/1e9;finish=output['finish_wall_ns']/1e9
    if not observed['started_monotonic']<=entry<=finish<=observed['finished_monotonic'] or output['finish_wall_ns']-output['entry_wall_ns']<row['budget_us']*1000:
        raise ValueError('Predecessor wall ordering or requested native duration failed')
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


def summarize(pairs):
    def values(pair):
        probe=pair['probe']['observation'];pred=pair['predecessor']['observation']
        return {'elapsed_seconds':probe['elapsed_seconds'],**probe['exclusive_phases_seconds'],
            'target_cpu_seconds':cpu_us(probe['accounting_receipt'])/1e6,'popen_offset_seconds':probe['popen_return_monotonic']-probe['started_monotonic'],
            **{key:probe['accounting_receipt'][key] for key in ('minor_faults','major_faults','block_inputs','block_outputs','voluntary_switches','involuntary_switches')},'gap_seconds':pair['actual_gap_seconds'],
            'predecessor_body_wall_seconds':pair['predecessor']['body_wall_seconds'],'predecessor_body_cpu_seconds':pair['predecessor']['body_cpu_seconds'],
            **{'predecessor_'+key:pred['accounting_receipt'][key] for key in ('minor_faults','major_faults','block_inputs','block_outputs','voluntary_switches','involuntary_switches')},
            'predecessor_wall_seconds':pred['elapsed_seconds'],'predecessor_cpu_seconds':cpu_us(pred['accounting_receipt'])/1e6,
            'predecessor_rss_bytes':pred['peak_rss_bytes'],'predecessor_raw_wait4_rss_bytes':pred['accounting_receipt']['target_rss_kib']*1024}
    def stats(raw):
        valid=[value for value in raw if value is not None]
        median=statistics.median(valid) if len(valid)==len(raw) else None;width=max(valid)-min(valid) if len(valid)==len(raw) else None
        return {'raw':raw,'unavailable_count':len(raw)-len(valid),'median':median,'range':width,'range_over_median':width/median if median else None}
    result={'groups':[],'contrasts':{}}
    for condition in range(4):
        group=[pair for pair in pairs if pair['condition']==condition]
        result['groups'].append({'condition':condition,'condition_code':chr(65+condition),
            'metrics':{key:stats([values(pair)[key] for pair in group]) for key in values(group[0])}})
    metrics=('elapsed_seconds','stream_preparation','launch_to_fork','fork_to_reap','report_to_end','target_cpu_seconds')
    contrasts={'A-B':{0:1,1:-1},'C-D':{2:1,3:-1},'A-C':{0:1,2:-1},'B-D':{1:1,3:-1},'(A-B)-(C-D)':{0:1,1:-1,2:-1,3:1}}
    for label,weights in contrasts.items():
        result['contrasts'][label]={}
        for metric in metrics:
            raw=[]
            for block in range(8):
                group={pair['condition']:pair for pair in pairs if pair['block']==block}
                raw.append(sum(weight*values(group[condition])[metric] for condition,weight in weights.items()))
            result['contrasts'][label][metric]={'raw_by_block':raw,'median':statistics.median(raw),'signs':[(v>0)-(v<0) for v in raw]}
    return result


def verify_production(root,manifest):
    provenance=json.loads((root/'accounting-provenance.json').read_text())
    filename='optimize-linux-accounting.py'
    if (manifest.get('production_evaluator')!=filename or provenance.get('production_filename')!=filename
        or manifest.get('production_evaluator_sha256')!=provenance.get('derivative_sha256')
        or sha(root/filename)!=provenance.get('derivative_sha256')
        or len(provenance.get('binding',{}))!=55 or manifest.get('production_bindings')!=provenance.get('binding')):
        raise RuntimeError('Production evaluator identity mismatch before import')
    return filename


def run(root,config):
    manifest=json.loads((root/'host-series-setup.json').read_text())
    filename=verify_production(root,manifest)
    if any(sha(root/name)!=value for name,value in manifest['diagnostic_files'].items()):raise RuntimeError('Diagnostic identity changed')
    module=load(root/filename,'_unchanged_production')
    accounting=load(root/'accounting_adapter.py','_unchanged_accounting')
    placement=load(root/'placement_controls.py','_unchanged_probe_validation')
    affinity=load(root/'placement_config.py','_unchanged_affinity')
    tool=manifest['taskset']
    if not Path(tool['path']).is_absolute() or sha(Path(tool['path']))!=tool['sha256']:raise RuntimeError('taskset identity changed before series')
    work=root/'host-control-work';work.mkdir()
    result={'planned_calls':64,'planned_pairs':32,'performance_selection':False,'passed':False,'pairs':[]}
    start=time.monotonic();stop=start+60;result.update(series_started_monotonic=start,series_deadline_monotonic=stop)
    module.save(root/'host-controls.json',result)
    def persist(pair,index):
        module.save(root/f'host-pair-{index:02d}.json',pair);module.save(root/'host-controls.json',result)
    try:
        for index,plan in enumerate(schedule()):
            pair=dict(plan,ordinal=index)
            try:
                for role in ('predecessor','probe'):
                    if role=='probe':
                        due=math.nextafter(pair['predecessor']['observation']['finished_monotonic']+0.002,math.inf)
                        while True:
                            now=time.monotonic()
                            if now>=stop:raise RuntimeError('Series deadline during diagnostic gap')
                            if now>=due:break
                            time.sleep(min(due-now,stop-now))
                    row=dict(plan,role=role);pair[role]=row
                    before=time.monotonic()
                    if before>=stop:raise RuntimeError('Diagnostic series deadline exhausted')
                    row['deadline_monotonic']=min(stop,before+3)
                    row['taskset_path']=tool['path']
                    row['parent_before']=affinity.require_parent(config['host_cpu'])
                    try:
                        row['observation']=module.measure(argv(root,row,config),work,module.runtime_environment(),row['deadline_monotonic'],capture_version=True,record_effects=False)
                    finally:row['parent_after']=sorted(os.sched_getaffinity(0))
                # Defer condition-specific work until the identical probe has finished.
                for role in ('predecessor','probe'):validate_call(pair[role],config,root,accounting,placement)
                gap=pair['probe']['observation']['started_monotonic']-pair['predecessor']['observation']['finished_monotonic']
                pair['actual_gap_seconds']=gap
                if not math.isfinite(gap) or gap<0.002:raise ValueError('Diagnostic gap shorter than 2 ms')
                if time.monotonic()>stop:raise RuntimeError('Diagnostic series exceeded 60 seconds')
            except BaseException as error:
                pair['functional_error']=repr(error);result['pairs'].append(pair);persist(pair,index)
                if not isinstance(error,Exception):raise
                raise RuntimeError('Host-location diagnostic functional failure; no FASTA comparison started') from error
            result['pairs'].append(pair);persist(pair,index)
    finally:
        result['series_finished_monotonic']=time.monotonic();result['parent_after']=sorted(os.sched_getaffinity(0))
        module.save(root/'host-controls.json',result)
    result['completed_calls']=sum('observation' in pair.get(role,{}) for pair in result['pairs'] for role in ('predecessor','probe'))
    result['summary']=summarize(result['pairs'])
    result['passed']=len(result['pairs'])==32 and result['completed_calls']==64 and result['series_finished_monotonic']<=stop and result['parent_after']==[config['host_cpu']]
    module.save(root/'host-controls.json',result)
    if not result['passed']:raise RuntimeError('Incomplete Host-location diagnostic; no FASTA comparison started')
    return result
