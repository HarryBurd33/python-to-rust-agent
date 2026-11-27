"""Fixed duration/link/output crossover; no production or performance selection changes."""
import hashlib
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import stat
import statistics
import time

SIZES=(544,169180)
HASHES=('d13f2c04dc3455dbee20eddac23f5da5bfa3e070898171168dbff0807b96e088','63a3be010ee47acaae0db51a3150bd315cad1e0b51ccc7bd7f9a3b7f26ddde5f')


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_production(root,manifest):
    original=json.loads((root/'accounting-provenance.json').read_text());name='optimize-linux-accounting.py'
    if (manifest.get('production_evaluator')!=name or original.get('production_filename')!=name
        or manifest.get('production_evaluator_sha256')!=original.get('derivative_sha256') or sha(root/name)!=original.get('derivative_sha256')
        or len(original.get('binding',{}))!=55 or manifest.get('production_bindings')!=original.get('binding')):
        raise RuntimeError('Production identity mismatch before import')
    return name


def schedule():
    return [{'block':block,'position':position,'condition':condition,'duration':condition>>2,'dynamic':(condition>>1)&1,'volume':condition&1}
            for block in range(8) for position,condition in enumerate([(value+block)%8 for value in (0,1,7,2,6,3,5,4)])]


def argv(root,row):
    if row['role']=='predecessor':return [str(root/'crossover-predecessor'),'sleeping',str((500,250000)[row['duration']])]
    return [str(root/('neutral-dynamic' if row['dynamic'] else 'neutral-static')),str(SIZES[row['volume']])]


def cpu_us(value):return (value['user_sec']+value['system_sec'])*1000000+value['user_usec']+value['system_usec']


def snapshot(value,counters):
    keys={'user_sec','user_usec','system_sec','system_usec'}|set(counters)
    if (not isinstance(value,dict) or set(value)!=keys or any(type(v) is not int or not 0<=v<(1<<63) for v in value.values())
        or value['user_usec']>=1000000 or value['system_usec']>=1000000):raise ValueError('Invalid self accounting units/counters')
    return cpu_us(value)


def validate(row,root,config,accounting,prior,placement):
    observed=row['observation'];output=json.loads(observed['version_output']);row['output']=output
    if (observed['argv']!=argv(root,row) or observed['placement_mode']!='split' or observed['timing_mode']!='pidfd'
        or observed['exit_code']!=0 or observed['timeout'] or observed['residual_process_group'] or observed['instrument_error']):raise ValueError('Diagnostic argv/status/mode failed')
    if row['parent_before']!=[config['host_cpu']] or row['parent_after']!=[config['host_cpu']]:raise ValueError('Parent left H')
    rss,mask,account=accounting.decode_accounting(observed['private_report_raw'].encode(),observed['supervisor_pid'],config['target_cpu'])
    if rss!=observed['rss_receipt'] or mask!=observed['placement_receipt'] or account!=observed['accounting_receipt']:raise ValueError('Private receipt mismatch')
    phases=accounting.phases(observed['started_monotonic'],observed['pre_popen_monotonic'],observed['popen_return_monotonic'],observed['finished_monotonic'],account)
    if phases!=observed['exclusive_phases_seconds'] or observed['elapsed_seconds']!=observed['finished_monotonic']-observed['started_monotonic'] or observed['finished_monotonic']>row['deadline_monotonic']:raise ValueError('Outer phase/deadline mismatch')
    raw=(observed['version_output']+'\n').encode()
    if len(raw)>2048 or observed['stdout']!={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} or observed['stderr']!={'size':0,'sha256':hashlib.sha256(b'').hexdigest()}:raise ValueError('Stdout/stderr mismatch')
    if row['role']=='predecessor':
        # Only the expected argv changes in this validation view; preserve raw evidence.
        viewed=dict(observed,argv=[str(root/'crossover-predecessor'),'sleeping',str((500,250000)[row['duration']])])
        original=dict(row,condition=1 if row['duration']==0 else 3,mode='sleeping',budget_us=(500,250000)[row['duration']],observation=viewed)
        prior.validate_call(original,config,root,accounting,placement)
        row.update(body_wall_seconds=original['body_wall_seconds'],body_cpu_seconds=original['body_cpu_seconds']);return
    expected={'pid','cpu_count','cpu','current_cpu','requested_bytes','written_bytes','entry_wall_ns','finish_wall_ns','U','V'}
    if set(output)!=expected or any(type(output[key]) is not int for key in expected-{'U','V'}):raise ValueError('Neutral output schema mismatch')
    size=SIZES[row['volume']];digest=HASHES[row['volume']]
    if (output['pid']!=rss['target_pid'] or output['cpu_count']!=1 or output['cpu']!=config['target_cpu'] or output['current_cpu']!=config['target_cpu']
        or output['requested_bytes']!=size or output['written_bytes']!=size):raise ValueError('Neutral PID/CPU/byte mismatch')
    f=account['fork_monotonic_ns'];i=output['entry_wall_ns'];o=output['finish_wall_ns'];r=account['reap_monotonic_ns']
    if not f<=i<=o<=r:raise ValueError('Neutral native clock ordering failed')
    u=snapshot(output['U'],accounting.COUNTERS);v=snapshot(output['V'],accounting.COUNTERS);q=cpu_us(account)
    if u>v+2 or v>q+2:raise ValueError('Self CPU exceeds rounding allowance')
    if any(not output['U'][key]<=output['V'][key]<=account[key] for key in accounting.COUNTERS):raise ValueError('Resource counter regression')
    cpu={'cpu_at_entry':u,'cpu_in_body':v-u,'cpu_after_body':q-v}
    if sum(cpu.values())!=q:raise ValueError('Signed CPU components do not sum')
    s=observed['started_monotonic'];l=observed['pre_popen_monotonic'];e=observed['finished_monotonic']
    wall={'preparation':l-s,'launch':f/1e9-l,'startup':(i-f)/1e9,'body':(o-i)/1e9,'after_body':(r-o)/1e9,'finish':e-r/1e9}
    if not math.isclose(math.fsum(wall.values()),e-s,abs_tol=1e-9,rel_tol=0):raise ValueError('Six wall terms do not reconstruct full elapsed')
    row['metrics']={'full_elapsed':e-s,**wall,'waited_cpu':q/1e6,**{key:value/1e6 for key,value in cpu.items()}}
    row['signed_cpu_components_us']=cpu
    cwd=Path(row['cwd']);target=cwd/'report.bin'
    if observed['cwd']!=str(cwd) or observed['effects']!=[['report.bin','file',0o600,size,digest]]:raise ValueError('Recorded cwd/effects mismatch')
    if {p.name for p in cwd.iterdir()}!={'report.bin'}:raise ValueError('Unexpected directory members')
    info=target.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size!=size:raise ValueError('Raw file type/mode/size mismatch')
    data=target.read_bytes()
    if data!=b'Z'*size or hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Raw output bytes/hash mismatch')


def summarize(pairs):
    result={'groups':[],'factorial_effects':{}}
    def stats(values):
        median=statistics.median(values);width=max(values)-min(values)
        return {'raw':values,'median':median,'range':width,'range_over_median':width/median if median else None}
    keys=tuple(pairs[0]['probe']['metrics'])
    for condition in range(8):
        group=[pair for pair in pairs if pair['condition']==condition]
        result['groups'].append({'condition':condition,'metrics':{key:stats([pair['probe']['metrics'][key] for pair in group]) for key in keys},
            'actual_gaps_seconds':[pair['actual_gap_seconds'] for pair in group],
            'predecessor_exposures':[{'elapsed':pair['predecessor']['observation']['elapsed_seconds'],'body_wall':pair['predecessor']['body_wall_seconds'],
                'body_cpu':pair['predecessor']['body_cpu_seconds'],'rss':pair['predecessor']['observation']['peak_rss_bytes'],
                'accounting':pair['predecessor']['observation']['accounting_receipt']} for pair in group]})
    for width in (1,2,3):
        for subset in itertools.combinations((('D',4),('K',2),('W',1)),width):
            label='x'.join(name for name,bit in subset);result['factorial_effects'][label]={}
            for key in keys:
                values=[]
                for block in range(8):
                    cells={pair['condition']:pair['probe']['metrics'][key] for pair in pairs if pair['block']==block}
                    total=sum(math.prod(1 if condition&bit else -1 for name,bit in subset)*value for condition,value in cells.items())
                    values.append(2**(width-3)*total)
                result['factorial_effects'][label][key]={'raw_by_block':values,'median':statistics.median(values),'signs':[(v>0)-(v<0) for v in values]}
    return result


def run(root,config):
    manifest=json.loads((root/'output-series-setup.json').read_text());filename=verify_production(root,manifest)
    if any(sha(root/name)!=value for name,value in manifest['diagnostic_files'].items()):raise RuntimeError('Diagnostic identity changed')
    module=load(root/filename,'_unchanged_production');accounting=load(root/'accounting_adapter.py','_same_accounting')
    prior=load(root/'python_first_controls.py','_same_native_predecessor');placement=load(root/'placement_controls.py','_same_target_checks');affinity=load(root/'placement_config.py','_same_affinity')
    parent=root/'neutral-output';parent.mkdir();directories=[]
    for index in range(64):
        cwd=parent/f'probe-{index:02d}';cwd.mkdir(mode=0o700)
        if any(cwd.iterdir()):raise RuntimeError('Probe cwd not fresh')
        directories.append(cwd)
    pred_work=root/'output-predecessor-work';pred_work.mkdir()
    result={'planned_calls':128,'planned_pairs':64,'performance_selection':False,'passed':False,'pairs':[]}
    start=time.monotonic();stop=start+60;result.update(series_started_monotonic=start,series_deadline_monotonic=stop)
    module.save(root/'output-controls.json',result)
    def persist(pair,index):module.save(root/f'output-pair-{index:02d}.json',pair);module.save(root/'output-controls.json',result)
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
                    cwd=directories[index] if role=='probe' else pred_work;row=dict(plan,role=role,cwd=str(cwd));pair[role]=row
                    before=time.monotonic()
                    if before>=stop:raise RuntimeError('Series deadline exhausted')
                    row['deadline_monotonic']=min(stop,before+3);row['parent_before']=affinity.require_parent(config['host_cpu'])
                    try:row['observation']=module.measure(argv(root,row),cwd,module.runtime_environment(),row['deadline_monotonic'],capture_version=True,record_effects=role=='probe')
                    finally:row['parent_after']=sorted(os.sched_getaffinity(0))
                for role in ('predecessor','probe'):validate(pair[role],root,config,accounting,prior,placement)
                gap=pair['probe']['observation']['started_monotonic']-pair['predecessor']['observation']['finished_monotonic'];pair['actual_gap_seconds']=gap
                if not math.isfinite(gap) or gap<0.002 or time.monotonic()>stop:raise RuntimeError('Gap/series bound failed')
            except BaseException as error:
                pair['functional_error']=repr(error);result['pairs'].append(pair);persist(pair,index)
                if not isinstance(error,Exception):raise
                raise RuntimeError('Neutral diagnostic failed; no FASTA comparison started') from error
            result['pairs'].append(pair);persist(pair,index)
    finally:
        result['series_finished_monotonic']=time.monotonic();result['parent_after']=sorted(os.sched_getaffinity(0));module.save(root/'output-controls.json',result)
    result['completed_calls']=sum('observation' in pair.get(role,{}) for pair in result['pairs'] for role in ('predecessor','probe'))
    result['summary']=summarize(result['pairs']);result['passed']=len(result['pairs'])==64 and result['completed_calls']==128 and result['series_finished_monotonic']<=stop and result['parent_after']==[config['host_cpu']]
    module.save(root/'output-controls.json',result)
    if not result['passed']:raise RuntimeError('Incomplete neutral diagnostics; no FASTA comparison started')
    return result
