"""Focused accounting/source/pair fixtures and mandatory actual-new-path native checks."""
import argparse
import ast
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE=Path(__file__).parent
ROOT=None
CONFIG={'host_cpu':2,'target_cpu':3,'initial_allowed_cpus':[0,1,2,3],'runner':'ubuntu-24.04-arm'}


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


placement=load(HERE/'placement_tests.py','preserved_placement_tests')
accounting=load(HERE/'accounting_adapter.py','accounting_adapter_test')
controls=load(HERE/'predecessor_controls.py','predecessor_controls_test')
sourcegen=load(HERE/'accounting_source.py','accounting_source_test')


def native_account(**changes):
    value={'accounting_version':1,'supervisor_pid':100,'target_pid':101,'wait_status':0,'target_rss_kib':4096,
        'fork_monotonic_ns':2250000000,'reap_monotonic_ns':3250000000,'user_sec':0,'user_usec':100000,'system_sec':0,'system_usec':0,
        **{key:5 for key in accounting.COUNTERS}}
    value.update(changes);return value


def payload(**changes):
    return placement.report()+json.dumps(native_account(**changes)).encode()+b'\n'


class SourceProtocolTests(unittest.TestCase):
    def test_exact_source_guard_child_and_rss_payload_unchanged(self):
        source=(HERE/'rss_supervisor.c').read_text();changed=sourcegen.generate(source)
        start=source.index('static void launch(');stop=source.index('\nint main(')
        self.assertEqual(source[start:stop],changed[changed.index('static void launch('):changed.index('\nint main(')])
        self.assertEqual(changed.count('pid_t target = fork();'),1)
        self.assertEqual(changed.count('result = wait4(target, &status, 0, &usage);'),1)
        self.assertEqual(changed.count('clock_gettime(CLOCK_MONOTONIC'),2)
        format_line=next(line.strip() for line in changed.splitlines() if 'accounting_version' in line and 'snprintf' not in line)
        import re
        decoded=json.loads(format_line.rstrip(','))
        example=json.loads(re.sub(r'%llu|%ld|%d','0',decoded))
        self.assertEqual(set(example),accounting.ACCOUNTING_KEYS)
        self.assertLess(changed.index('write_all((int)report, output,'),changed.index('char accounting[1024]'))
        with self.assertRaisesRegex(ValueError,'identity changed'):sourcegen.generate(source+'\n')

    def test_strict_three_lines_and_same_original_receipts(self):
        with patch.object(accounting.os,'sysconf',return_value=4096):
            rss,cpu,account=accounting.decode_accounting(payload(),100,3)
            original_rss,original_cpu=accounting.decode_placement(placement.report(),100,3)
        self.assertEqual(rss,original_rss);self.assertEqual(cpu,original_cpu);self.assertEqual(account['target_pid'],rss['target_pid'])
        bad=[payload()[:-1],placement.report(),payload()+b'{}\n',b'x'*1697,payload(target_pid=102),payload(wait_status=2<<8),
            payload(user_usec=1000000),payload(system_sec=-1),payload(minor_faults=True),payload(block_inputs=1<<63),
            payload(fork_monotonic_ns=4000000000),payload(target_rss_kib=8192),payload().replace(b'"accounting_version": 1',b'"accounting_version":1,"accounting_version":1')]
        for raw in bad:
            with self.subTest(raw=raw),patch.object(accounting.os,'sysconf',return_value=4096),self.assertRaises(ValueError):
                accounting.decode_accounting(raw,100,3)

    def test_phase_order_reconstruction_and_no_p_order_assumption(self):
        account=native_account()
        phases=accounting.phases(1,2,2.1,4,account);self.assertEqual(sum(phases.values()),3)
        self.assertEqual(phases,accounting.phases(1,2,3.8,4,account))
        for args in ((2,1,2.1,4),(1,2.3,2.5,4),(1,2,2.5,3),(1,2,1.5,4)):
            with self.assertRaises(ValueError):accounting.phases(*args,account)

    def test_actual_adapter_pre_popen_and_full_endpoint_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);work=root/'work';work.mkdir()
            module={'__file__':str(root/'derivative.py'),'_PLACEMENT':CONFIG,'_TIMING_MODE':'pidfd','_PLACEMENT_MODE':'split',
                'require':lambda v,m:None,'wait_for_child':lambda c,d:(0,77,False),'process_group_exists':lambda pid:False,
                'kill_group':lambda pid:None,'stream_signature':lambda path:{'size':path.stat().st_size},'tree':lambda path:[],
                'save':lambda path,value:path.write_text(json.dumps(value)),'Uncertain':RuntimeError}
            def popen(argv,**kwargs):
                self.assertEqual(Path(argv[0]).name,'accounting-supervisor');self.assertEqual(argv[3:],['/target','argument'])
                os.write(kwargs['pass_fds'][0],payload());return SimpleNamespace(pid=100,returncode=None)
            with patch.object(accounting.subprocess,'Popen',side_effect=popen),patch.object(accounting.os,'sysconf',return_value=4096), \
                 patch.object(accounting.time,'monotonic',side_effect=[0,1,2,2.5,4]):
                row=accounting.measure(module,['/target','argument'],work,{},10)
            self.assertEqual(row['pre_popen_monotonic'],2);self.assertEqual(row['elapsed_seconds'],3)
            self.assertEqual(row['exclusive_phases_seconds'],accounting.phases(1,2,2.5,4,native_account()))
            self.assertEqual(row['rss_receipt_raw'],placement.rss.receipt().decode()+'\n')
            self.assertEqual(row['accounting_receipt']['target_rss_kib'],row['wait4_ru_maxrss'])

    def test_new_host_guard_stays_stable_and_main_sequence_unchanged(self):
        build=load(HERE/'accounting_build.py','new_accounting_builder')
        place=load(HERE/'placement_build.py','old_place_builder');timing=load(HERE/'timing_build.py','old_timing_builder');rss=load(HERE/'rss_build.py','old_rss_builder')
        if ROOT:original=(ROOT/'optimize.py').read_text()
        else:
            import base64,zlib
            bundle=json.loads((HERE/'bundle.json').read_text());original=zlib.decompress(base64.b64decode(bundle['files']['optimize.py']['zlib_base64'])).decode()
        derived=build.generate(place,timing,rss,original,{},CONFIG)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'derivative.py';path.write_text(derived)
            for name in ('timing_wait.py','accounting_adapter.py','placement_adapter.py','rss_adapter.py'):(root/name).write_bytes((HERE/name).read_bytes())
            with patch.object(os,'sched_getaffinity',return_value={2},create=True):module=load(path,'actual_accounting_host_guard')
            env={'PATH':'fixed'}
            with patch.object(os,'getpid',return_value=100):baseline=module.host_info(env)
            with patch.object(os,'getpid',return_value=200):self.assertEqual(baseline,module.host_info(env))
            directory=root/'evaluation';directory.mkdir()
            class ReachedVersions(Exception):pass
            with patch.object(module,'runtime_environment',return_value=env),patch.object(module,'copy_tree'),patch.object(module,'observe_versions',side_effect=ReachedVersions):
                with self.assertRaises(ReachedVersions):module.collect_evaluation(root,directory,{}, {}, {'host':baseline},root/'origin',root/'candidate','0'*64,10)
        old=ast.parse((HERE/'run_small.py').read_text());new=ast.parse((HERE/'run_accounting.py').read_text())
        oldmain=next(node for node in old.body if isinstance(node,ast.FunctionDef) and node.name=='main')
        newmain=next(node for node in new.body if isinstance(node,ast.FunctionDef) and node.name=='main')
        for node in ast.walk(newmain):
            if isinstance(node,ast.Constant) and isinstance(node.value,str) and node.value.startswith('Native-accounting split comparison'):
                node.value='Split logical-CPU comparison; independent review required, no automatic adoption.'
        self.assertEqual(ast.dump(oldmain,include_attributes=False),ast.dump(newmain,include_attributes=False))


class PairTests(unittest.TestCase):
    @staticmethod
    def observation(root,role,mode,start=1.0):
        duration=0.251 if role=='predecessor' else 0.001
        launch=start+0.0001;fork=start+0.0002;end=start+duration;reap=end-0.0001
        account=native_account(fork_monotonic_ns=round(fork*1e9),reap_monotonic_ns=round(reap*1e9),user_usec=100000 if role=='predecessor' else 500)
        entry=round((fork+0.00001)*1e9);finish=entry+(250000000 if role=='predecessor' else 10000)
        if role=='predecessor':
            output={'mode':mode,'entry_wall_ns':entry,'finish_wall_ns':finish,'entry_cpu_ns':1000,'finish_cpu_ns':99001000,
                'target_pid':101,'cpu_count':1,'cpu':3,'current_cpu':3,
                'self_rusage':{'user_sec':0,'user_usec':99500,'system_sec':0,'system_usec':0,**{key:4 for key in accounting.COUNTERS}}}
        else:output={'budget_us':0,'entry_wall_ns':entry,'finish_wall_ns':finish,'entry_cpu_ns':1000,'finish_cpu_ns':2000,'target_pid':101,'cpu_count':1,'cpu':3,'current_cpu':3}
        text=json.dumps(output);raw=(text+'\n').encode()
        return {'argv':[str(root/'predecessor-control'),mode] if role=='predecessor' else [str(root/'placement-control'),'0'],
            'placement_mode':'split','timing_mode':'pidfd','exit_code':0,'timeout':False,'residual_process_group':False,'instrument_error':None,
            'placement_receipt':{'cpu':3,'cpu_count':1,'supervisor_pid':100},'rss_receipt':{'target_pid':101,'floor_limited':True},
            'accounting_receipt':account,'started_monotonic':start,'pre_popen_monotonic':launch,'popen_return_monotonic':start+0.0003,
            'finished_monotonic':end,'elapsed_seconds':end-start,'exclusive_phases_seconds':accounting.phases(start,launch,start+0.0003,end,account),
            'stdout':{'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()},'stderr':{'size':0,'sha256':hashlib.sha256(b'').hexdigest()},'version_output':text}

    def test_schedule_and_accounting_self_checks(self):
        rows=controls.schedule();self.assertEqual(len(rows),32)
        for block in range(16):self.assertEqual([row['mode'] for row in rows if row['block']==block],['busy','sleeping'] if block%2==0 else ['sleeping','busy'])
        root=Path('/fixture');obs=self.observation(root,'predecessor','busy')
        row={'role':'predecessor','mode':'busy','observation':obs,'parent_before':[2],'parent_after':[2],'deadline_monotonic':4}
        original=load(HERE/'placement_controls.py','old_probe_validation')
        controls.validate_call(row,CONFIG,root,accounting,original)
        for field,value in (('user_usec',200000),('minor_faults',6),('system_usec',1000000)):
            output=json.loads(obs['version_output']);output['self_rusage'][field]=value
            changed=dict(obs,version_output=json.dumps(output));raw=(changed['version_output']+'\n').encode();changed['stdout']={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
            bad=dict(row,observation=changed)
            with self.assertRaises(ValueError):controls.validate_call(bad,CONFIG,root,accounting,original)
        short=dict(row,observation=dict(obs,accounting_receipt=native_account(user_usec=1)))
        with self.assertRaises(ValueError):controls.validate_call(short,CONFIG,root,accounting,original)

    def test_target_body_must_fit_native_marks_not_only_parent_timer(self):
        root=Path('/fixture');old=load(HERE/'placement_controls.py','crosscheck_probe_validation')
        for role in ('predecessor','probe'):
            observed=self.observation(root,role,'busy');output=json.loads(observed['version_output'])
            output['entry_wall_ns']=observed['accounting_receipt']['fork_monotonic_ns']-1
            observed['version_output']=json.dumps(output);raw=(observed['version_output']+'\n').encode()
            observed['stdout']={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
            row={'role':role,'mode':'busy','parent_before':[2],'parent_after':[2],'deadline_monotonic':4,'observation':observed}
            with self.assertRaisesRegex(ValueError,'outside native'):controls.validate_call(row,CONFIG,root,accounting,old)

    def test_minimum_gap_and_partial_pair_preservation(self):
        pair={'predecessor':{'observation':{'finished_monotonic':1}},'probe':{'observation':{'started_monotonic':1.001}}}
        with self.assertRaises(ValueError):controls.validate_gap(pair)
        pair['probe']['observation']['started_monotonic']=1.0021;controls.validate_gap(pair)
        proof,events=self.run_fixture(fail_probe=True)
        self.assertFalse(proof['passed']);self.assertEqual(len(proof['pairs']),1)
        self.assertIn('observation',proof['pairs'][0]['predecessor']);self.assertIn('functional_error',proof['pairs'][0])
        self.assertEqual([event for event in events if event.startswith('measure')],['measure predecessor','measure probe'])

    def run_fixture(self,fail_probe=False):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);root=Path(temporary.name);clock=[1.0];events=[]
        def save(path,value):events.append('save '+path.name);path.write_text(json.dumps(value))
        module=SimpleNamespace(save=save,runtime_environment=lambda:{})
        def measure(argv,*args,**kwargs):
            role='predecessor' if Path(argv[0]).name=='predecessor-control' else 'probe';mode=argv[1] if role=='predecessor' else 'probe'
            events.append('measure '+role)
            if role=='probe' and fail_probe:raise RuntimeError('probe fixture failed')
            row=self.observation(root,role,mode,clock[0]);clock[0]=row['finished_monotonic'];return row
        module.measure=measure
        old=load(HERE/'placement_controls.py','fixture_original_probe_validation')
        affinity=SimpleNamespace(require_parent=lambda cpu:[cpu])
        with patch.object(controls,'load',side_effect=[module,accounting,old,affinity]),patch.object(controls.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(controls.time,'sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)),patch.object(controls.os,'sched_getaffinity',return_value={2},create=True):
            if fail_probe:
                with self.assertRaisesRegex(RuntimeError,'functional failure'):controls.run(root,CONFIG)
            else:self.assertTrue(controls.run(root,CONFIG)['passed'])
        return json.loads((root/'predecessor-controls.json').read_text()),events

    def test_all64_no_aggregate_between_pairs_and_no_selection(self):
        proof,events=self.run_fixture();self.assertEqual(proof['completed_calls'],64);self.assertEqual(len(proof['pairs']),32)
        self.assertFalse(proof['performance_selection']);self.assertEqual(len(proof['summary']['groups']),2)
        for index,event in enumerate(events):
            if event=='measure predecessor':self.assertEqual(events[index+1],'measure probe')
        self.assertTrue(all(pair['actual_gap_seconds']>=0.002 for pair in proof['pairs']))


class ActualAccountingNative(placement.NewPathRSSNative):
    def setUp(self):
        if ROOT is None:self.skipTest('Actual accounting Linux integration requires --root')
        placement.ROOT=ROOT;placement.rss.ROOT=ROOT
        self.config=json.loads((ROOT/'placement-config.json').read_text())
        self.module=load(ROOT/'optimize-linux-accounting.py','actual_accounting_native')
        self.assertEqual(self.module._PLACEMENT_STARTUP_CPUS,[self.config['host_cpu']])
        self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.addCleanup(self.temp.cleanup);self.cwd=Path(self.temp.name);self.environment=self.module.runtime_environment()

    def test_native_accounting_same_receipt_and_exclusive_phases(self):
        result=self.call(['exit','0']);account=result['accounting_receipt']
        self.assertEqual(account['target_pid'],result['rss_receipt']['target_pid']);self.assertEqual(account['target_rss_kib'],result['wait4_ru_maxrss'])
        self.assertEqual(result['elapsed_seconds'],result['finished_monotonic']-result['started_monotonic'])
        self.assertAlmostEqual(sum(result['exclusive_phases_seconds'].values()),result['elapsed_seconds'],places=8)
        self.assertGreaterEqual(account['reap_monotonic_ns'],account['fork_monotonic_ns'])

    def test_already_exited_new_supervisor_and_private_evidence_fd_closed(self):
        wait=load(ROOT/'timing_wait.py','accounting_already_exited_wait')
        before=len(list(Path('/proc/self/fd').iterdir()))
        with tempfile.TemporaryFile() as stream:
            target=str(ROOT/'rss-fixture');child=subprocess.Popen([str(ROOT/'accounting-supervisor'),str(stream.fileno()),target,target,'exit','127'],start_new_session=True,pass_fds=(stream.fileno(),),env=self.environment)
            reaped=False
            try:
                stop=time.monotonic()+3;terminated=None
                while time.monotonic()<stop:
                    terminated=os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOWAIT|os.WNOHANG)
                    if terminated is not None and terminated.si_pid==child.pid:break
                    time.sleep(0.001)
                self.assertIsNotNone(terminated);self.assertEqual(terminated.si_pid,child.pid)
                code,usage,expired=wait.wait_for_child(child,time.monotonic()+3,lambda pid:os.killpg(pid,signal.SIGKILL));child.returncode=code;reaped=True
                self.assertEqual(code,0);self.assertFalse(expired);stream.seek(0)
                rss,cpu,account=accounting.decode_accounting(stream.read(1697),child.pid,self.config['target_cpu'])
                self.assertEqual(rss['exit_code'],127);self.assertEqual(account['wait_status'],127<<8)
            finally:
                if not reaped:
                    with contextlib.suppress(ProcessLookupError):os.killpg(child.pid,signal.SIGKILL)
                    with contextlib.suppress(ChildProcessError):child.wait(timeout=3)
        self.assertEqual(before,len(list(Path('/proc/self/fd').iterdir())))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path);args=parser.parse_args();ROOT=args.root.resolve() if args.root else None
    suite=unittest.TestSuite()
    for case in (placement.LegacyRSSProtocol,placement.timing.WaitTests,SourceProtocolTests,PairTests,ActualAccountingNative):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    result=unittest.TextTestRunner(verbosity=2).run(suite);raise SystemExit(0 if result.wasSuccessful() else 1)
