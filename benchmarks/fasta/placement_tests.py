"""Legacy-only protocol checks, actual new-path native tests, and pure placement fixtures."""
import argparse
import ast
import base64
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
from unittest.mock import Mock,patch
import zlib

HERE=Path(__file__).parent
ROOT=None
CONFIG={'host_cpu':2,'target_cpu':3,'initial_allowed_cpus':[0,1,2,3],'runner':'ubuntu-24.04-arm'}


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


rss=load(HERE/'test_rss.py','legacy_rss_tests')
timing=load(HERE/'timing_tests.py','legacy_timing_tests')
adapter=load(HERE/'placement_adapter.py','placement_adapter_test')
choices=load(HERE/'placement_config.py','placement_choices_test')
controls=load(HERE/'placement_controls.py','placement_controls_test')
readiness=load(HERE/'placement_readiness.py','placement_ready_test')


def report(**changes):
    header={'placement_version':1,'supervisor_pid':100,'cpu':3,'cpu_count':1};header.update(changes)
    return json.dumps(header).encode()+b'\n'+rss.receipt()+b'\n'


class LegacyRSSProtocol(rss.ProtocolTests):
    """Unchanged original protocol unit fixture; explicitly legacy-only."""


class PlacementTests(unittest.TestCase):
    def test_choices_max_and_second_max_with_bad_inputs(self):
        self.assertEqual(choices.choose({0,3,1,2}),CONFIG)
        for value in ([],[0],[0,0],[0,1024],[-1,2],[False,3]):
            with self.subTest(value=value),self.assertRaises(RuntimeError):choices.choose(value)

    def test_pin_h_and_direct_child_inheritance(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(choices.os,'sched_getaffinity',side_effect=[{0,1,2,3},{2}],create=True), \
                 patch.object(choices.os,'sched_setaffinity',create=True) as setmask, \
                 patch.object(choices.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='[2]\n',stderr='')):
                result=choices.pin(root,lambda path,value:path.write_text(json.dumps(value)))
            self.assertEqual(result,CONFIG);setmask.assert_called_once_with(0,{2})
            self.assertTrue(json.loads((root/'placement-affinity.json').read_text())['passed'])

    def test_parent_readback_and_retention_fail_closed(self):
        with patch.object(choices.os,'sched_getaffinity',return_value={1},create=True),patch.object(choices.os,'sched_setaffinity',create=True):
            with self.assertRaisesRegex(RuntimeError,'readback'):choices.set_parent(2)
            with self.assertRaisesRegex(RuntimeError,'retain'):choices.require_parent(2)

    def test_per_call_header_uses_original_rss_decoder(self):
        with patch.object(adapter.os,'sysconf',return_value=4096):
            target,placement=adapter.decode_placement(report(),100,3)
        self.assertEqual(target['target_pid'],101);self.assertEqual(target['target_rss_bytes'],4096*1024)
        self.assertEqual(placement['cpu'],3)
        invalid=[rss.receipt(),report()[:-1],report()+b'\n',b'x'*673,report(cpu=2),report(cpu_count=2),report(supervisor_pid=99),
                 report(cpu=True),report().replace(b'"placement_version": 1',b'"placement_version":1,"placement_version":1')]
        for payload in invalid:
            with self.subTest(payload=payload),patch.object(adapter.os,'sysconf',return_value=4096),self.assertRaises(ValueError):
                adapter.decode_placement(payload,100,3)

    def test_actual_adapter_endpoints_placement_raw_and_exact_argv(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);work=root/'work';work.mkdir()
            module={'__file__':str(root/'derived.py'),'_PLACEMENT':CONFIG,'_PLACEMENT_MODE':'split','_TIMING_MODE':'pidfd',
                'require':lambda value,message:None,'wait_for_child':lambda child,deadline:(0,123,False),
                'process_group_exists':lambda pid:False,'kill_group':lambda pid:None,'stream_signature':lambda path:{'size':path.stat().st_size},
                'tree':lambda path:[],'save':lambda path,value:path.write_text(json.dumps(value)),'Uncertain':RuntimeError}
            def popen(argv,**kwargs):
                self.assertEqual(Path(argv[0]).name,'placement-supervisor');self.assertEqual(argv[3:],['/target','arg'])
                self.assertTrue(kwargs['start_new_session']);self.assertEqual(kwargs['stdin'],subprocess.DEVNULL)
                os.write(kwargs['pass_fds'][0],report());return SimpleNamespace(pid=100,returncode=None)
            with patch.object(adapter.subprocess,'Popen',side_effect=popen),patch.object(adapter.os,'sysconf',return_value=4096), \
                 patch.object(adapter.time,'monotonic',side_effect=[0,1,2,3]):
                result=adapter.measure(module,['/target','arg'],work,{},10)
            self.assertEqual(result['elapsed_seconds'],2);self.assertEqual(result['popen_return_monotonic'],2)
            self.assertEqual(result['placement_receipt']['supervisor_pid'],100)
            self.assertEqual(result['rss_receipt_raw'],rss.receipt().decode()+'\n')
            self.assertEqual(result['placement_mode'],'split');self.assertEqual(result['peak_rss_bytes'],4096*1024)

    def test_bad_header_saved_and_clock_interruption_cleans_group(self):
        for failure in ('header','clock'):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);work=root/'work';work.mkdir();killed=[]
                module={'__file__':str(root/'derived.py'),'_PLACEMENT':CONFIG,'require':lambda v,m:None,
                    'wait_for_child':lambda c,d:(0,1,False),'kill_group':killed.append,'process_group_exists':lambda pid:False,
                    'stream_signature':lambda path:{},'tree':lambda path:[],'save':lambda path,value:path.write_text(json.dumps(value)),'Uncertain':RuntimeError}
                def popen(argv,**kwargs):os.write(kwargs['pass_fds'][0],report(cpu=2));return SimpleNamespace(pid=100,returncode=None)
                clock=[0,1,KeyboardInterrupt()] if failure=='clock' else [0,1,2,3]
                with patch.object(adapter.subprocess,'Popen',side_effect=popen),patch.object(adapter.time,'monotonic',side_effect=clock), \
                     patch.object(adapter.os,'wait4',return_value=(100,0,None)) as reap:
                    with self.assertRaises(KeyboardInterrupt if failure=='clock' else RuntimeError):adapter.measure(module,['/target'],work,{},10)
                if failure=='clock':self.assertEqual(killed,[100]);reap.assert_called_once_with(100,0)
                else:
                    paths=list(root.glob('rss-invalid-*.json'));self.assertEqual(len(paths),1)
                    record=json.loads(paths[0].read_text());self.assertIsNone(record['exit_code']);self.assertIn('cpu',record['private_report_raw'])

    def test_generated_identity_guard_actual_startup_and_unchanged_pidfd(self):
        old=load(HERE/'timing_build.py','old_timing_builder');rssbuild=load(HERE/'rss_build.py','old_rss_builder')
        new=load(HERE/'placement_build.py','placement_builder_test')
        if ROOT:source=(ROOT/'optimize.py').read_text()
        else:
            bundle=json.loads((HERE/'bundle.json').read_text());source=zlib.decompress(base64.b64decode(bundle['files']['optimize.py']['zlib_base64'])).decode()
        derived=new.generate(old,rssbuild,source,{},CONFIG)
        ast.parse(derived)
        self.assertIn('_PLACEMENT_STARTUP_CPUS',derived);self.assertIn('"cpu_placement": dict(',derived)
        def functions(text):return {node.name:ast.dump(node,include_attributes=False) for node in ast.parse(text).body if isinstance(node,ast.FunctionDef)}
        prior=old.generate(rssbuild,source,{},'pidfd')
        a,b=functions(prior),functions(derived)
        for name in set(a)-{'host_info','report_evaluation'}:self.assertEqual(a[name],b[name],name)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'derivative.py';path.write_text(derived)
            with patch.object(os,'sched_getaffinity',return_value={3},create=True):
                with self.assertRaisesRegex(ValueError,'start on H'):load(path,'bad_startup_placement')


    def test_actual_host_equality_across_processes_reaches_frozen_version_gate(self):
        old=load(HERE/'timing_build.py','host_old_timing_builder');rssbuild=load(HERE/'rss_build.py','host_old_rss_builder')
        new=load(HERE/'placement_build.py','host_new_placement_builder')
        if ROOT:source=(ROOT/'optimize.py').read_text()
        else:
            bundle=json.loads((HERE/'bundle.json').read_text());source=zlib.decompress(base64.b64decode(bundle['files']['optimize.py']['zlib_base64'])).decode()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);derived=root/'derivative.py';derived.write_text(new.generate(old,rssbuild,source,{},CONFIG))
            for name in ('timing_wait.py','placement_adapter.py','rss_adapter.py'):(root/name).write_bytes((HERE/name).read_bytes())
            with patch.object(os,'sched_getaffinity',return_value={2},create=True):module=load(derived,'actual_generated_host_equality')
            env={'PATH':'fixed fixture path','PYTHONHASHSEED':'0'}
            with patch.object(os,'getpid',return_value=100):baseline_host=module.host_info(env)
            with patch.object(os,'getpid',return_value=200):evaluation_host=module.host_info(env)
            self.assertEqual(baseline_host,evaluation_host)
            self.assertEqual(baseline_host['cpu_placement']['startup_cpus'],[2])
            self.assertNotIn('evaluator_pid',baseline_host['cpu_placement'])
            directory=root/'evaluation';directory.mkdir()
            class ReachedFrozenVersionGate(Exception):pass
            with patch.object(module,'runtime_environment',return_value=env),patch.object(module,'copy_tree'), \
                 patch.object(module,'observe_versions',side_effect=ReachedFrozenVersionGate),patch.object(os,'getpid',return_value=200):
                with self.assertRaises(ReachedFrozenVersionGate):
                    module.collect_evaluation(root,directory,{}, {}, {'host':baseline_host},root/'origin',root/'candidate','0'*64,10)
            with patch.object(os,'sched_getaffinity',return_value={3},create=True):
                with self.assertRaisesRegex(ValueError,'start on H'):load(derived,'actual_wrong_startup_host')


class ReadinessTests(unittest.TestCase):
    @staticmethod
    def raw(tick,host=90,target=90):
        def cpu(label,idle):return label+' '+f'{(100-idle)*tick} 0 0 {idle*tick} 0 0 0 0 999 999\n'
        return {'stat':cpu('cpu',90)+cpu('cpu2',host)+cpu('cpu3',target),'vmstat':'pswpin 0\npswpout 0\n','errors':{}}

    def test_global_and_both_cpus_required(self):
        for host,target,passed in ((90,90,True),(84,90,False),(90,84,False)):
            a=readiness.parse_snapshot(self.raw(1,host,target),CONFIG);b=readiness.parse_snapshot(self.raw(2,host,target),CONFIG)
            self.assertEqual(set(a['cpus']),{'cpu','cpu2','cpu3'})
            self.assertEqual(readiness.legacy.interval(a,b)['passed'],passed)
        bad=self.raw(1);bad['stat']=bad['stat'].replace('cpu2','cpu8')
        with self.assertRaises(ValueError):readiness.parse_snapshot(bad,CONFIG)

    def test_six_failures_keep_36_snapshots_and_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);counter=iter([self.raw(i,target=80) for _ in range(6) for i in range(6)])
            def capture(path,name):
                raw=next(counter);(path/name).write_text(json.dumps(raw));return raw
            with patch.object(readiness.legacy,'snapshot',side_effect=capture),patch.object(readiness.time,'sleep') as sleep:
                with self.assertRaisesRegex(RuntimeError,'no performance run'):readiness.check_readiness(root,CONFIG)
            proof=json.loads((root/'readiness.json').read_text());self.assertFalse(proof['passed']);self.assertEqual(len(proof['windows']),6)
            self.assertEqual(len(list(root.glob('*-sample-*.json'))),36);self.assertEqual([c.args[0] for c in sleep.call_args_list].count(20),5)


class ControlTests(unittest.TestCase):
    def test_schedule_exact_balanced(self):
        rows=controls.schedule();self.assertEqual(len(rows),64)
        for block in range(16):
            group=[row for row in rows if row['block']==block]
            self.assertEqual([row['duration_index'] for row in group],[0,0,1,1] if block%2==0 else [1,1,0,0])
            for duration in (0,1):
                first=next(row for row in group if row['duration_index']==duration)
                self.assertEqual(first['mode'],'shared' if (block//2+duration)%2==0 else 'split')
        for duration in (0,1):
            for position in (0,1):
                first=[row for row in rows if row['duration_index']==duration and row['duration_position']==position and row['order_in_pair']==0]
                self.assertEqual(sum(row['mode']=='shared' for row in first),4)

    @staticmethod
    def observation(mode,budget):
        entry=1000100000;finish=entry+budget*1000+10000;end=finish/1e9+0.001+(0.001 if mode=='split' else 0)
        output={'budget_us':budget,'entry_wall_ns':entry,'finish_wall_ns':finish,'entry_cpu_ns':10000,'finish_cpu_ns':10000+budget*1000,
            'target_pid':101,'cpu_count':1,'cpu':3,'current_cpu':3}
        raw=(json.dumps(output)+'\n').encode()
        return {'stdout':{'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()},'stderr':{'size':0,'sha256':hashlib.sha256(b'').hexdigest()},
            'placement_mode':mode,'timing_mode':'pidfd','exit_code':0,'timeout':False,'residual_process_group':False,'instrument_error':None,
            'rss_receipt':{'target_pid':101,'floor_limited':True},'placement_receipt':{'cpu':3,'supervisor_pid':100,'cpu_count':1},
            'started_monotonic':1.0,'popen_return_monotonic':1.0005,'finished_monotonic':end,'elapsed_seconds':end-1.0,'version_output':json.dumps(output)}

    def fixture(self,fail=False):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);root=Path(temporary.name)
        state=[2];sets=[];calls=[]
        module=SimpleNamespace(_PLACEMENT_MODE='split',save=lambda path,value:path.write_text(json.dumps(value)),runtime_environment=lambda:{})
        def set_parent(cpu):state[:]=[cpu];sets.append(cpu);return [cpu]
        affinity=SimpleNamespace(set_parent=set_parent)
        def measure(argv,*args,**kwargs):
            calls.append((module._PLACEMENT_MODE,int(argv[1]),args[2]))
            if fail:raise RuntimeError('functional fixture')
            return self.observation(module._PLACEMENT_MODE,int(argv[1]))
        module.measure=measure
        with patch.object(controls,'load',side_effect=[module,affinity]),patch.object(controls.os,'sched_getaffinity',side_effect=lambda pid:set(state),create=True), \
             patch.object(controls.time,'monotonic',return_value=1):
            if fail:
                with self.assertRaisesRegex(RuntimeError,'functional failure'):controls.run(root,CONFIG)
            else:self.assertTrue(controls.run(root,CONFIG)['passed'])
        proof=json.loads((root/'placement-controls.json').read_text())
        return proof,calls,sets,module

    def test_all64_even_when_split_slower_and_h_restored(self):
        proof,calls,sets,module=self.fixture()
        self.assertEqual(len(calls),64);self.assertEqual(sets[-1],2);self.assertEqual(proof['restored_parent'],[2])
        self.assertTrue(all(deadline==4 for mode,budget,deadline in calls));self.assertEqual(len(proof['summary']['groups']),4)
        self.assertFalse(proof['performance_selection']);self.assertEqual(module._PLACEMENT_MODE,'split')

    def test_failure_retained_stops_and_restores_h(self):
        proof,calls,sets,module=self.fixture(fail=True)
        self.assertEqual(len(calls),1);self.assertEqual(sets[-1],2);self.assertFalse(proof['passed'])
        self.assertIn('functional_error',proof['records'][0]);self.assertEqual(proof['restored_parent'],[2])

    def test_output_pid_cpu_budget_and_parent_validation(self):
        observed=self.observation('split',500)
        base={'mode':'split','budget_us':500,'parent_before':[2],'parent_after':[2],'deadline_monotonic':4,'observation':observed,'output':json.loads(observed['version_output'])}
        controls.validate(base,CONFIG)
        for key,value in (('target_pid',102),('current_cpu',2),('cpu_count',2),('finish_cpu_ns',0)):
            row=dict(base,output=dict(base['output'],**{key:value}))
            with self.assertRaises(ValueError):controls.validate(row,CONFIG)
        for field,bad in (('stdout',{'size':0,'sha256':'0'*64}),('stderr',{'size':1,'sha256':'0'*64})):
            row=dict(base,observation=dict(observed,**{field:bad}))
            with self.assertRaisesRegex(ValueError,'raw stdout/stderr'):controls.validate(row,CONFIG)



class RunnerTests(unittest.TestCase):
    def fixture(self,fail=None):
        runner=load(HERE/'run_small.py','small_runner_fixture')
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);root=Path(temporary.name).resolve()/'restored';events=[]
        def qualify(path,config):
            events.append('qualify_and_64controls')
            if fail=='qualify':raise RuntimeError('qualify failure')
            return path/'optimize-linux-split.py'
        def ready(path,config):
            events.append('global_h_t_quiet')
            if fail=='quiet':raise RuntimeError('quiet failure')
        affinity=SimpleNamespace(pin=lambda root,save:CONFIG,require_parent=lambda cpu:[cpu])
        loader=lambda path,name:affinity if name=='_selected_cpu_choices' else SimpleNamespace(check_readiness=ready)
        def command(argv,**kwargs):
            action=argv[2];events.append(action)
            self.assertEqual(Path(argv[1]).name,'optimize-linux-split.py');self.assertEqual(kwargs['timeout'],240)
            self.assertEqual(argv[3:],[str(root/'contract.json'),'--runs',str(root/'runs'),'--expect-contract',runner.legacy.CONTRACT]+(['--candidate',str(root/'candidate')] if action=='evaluate' else []))
            if action=='evaluate':
                report=root/'fixture-report.json';report.write_text(json.dumps({'status':'fixture','parity':True,'workloads':[]}));kwargs['stdout'].write(json.dumps({'report_json':str(report)}))
            return SimpleNamespace(returncode=1 if fail==action else 0)
        with patch.object(sys,'argv',['run_small.py','--destination',str(root)]),patch.object(runner.platform,'system',return_value='Linux'), \
             patch.object(runner.platform,'machine',return_value='aarch64'),patch.object(sys,'version_info',(3,15,0)), \
             patch.object(runner,'load',side_effect=loader),patch.object(runner,'qualify',side_effect=qualify), \
             patch.object(runner.legacy.frozen,'compiler_preflight'),patch.object(runner.legacy.frozen,'capture'),patch.object(runner.legacy,'host_info'), \
             patch.object(runner.legacy,'snapshot',side_effect=lambda path,name:events.append('posthost')), \
             patch.object(runner.os,'sched_getaffinity',return_value={2},create=True),patch.object(runner.subprocess,'run',side_effect=command):
            try:runner.main()
            finally:self.events=events;self.root=root

    def test_one_sequence_only_after_qualification_controls_and_three_way_quiet(self):
        self.fixture();self.assertEqual(self.events,['qualify_and_64controls','global_h_t_quiet','baseline','evaluate','posthost'])
        self.assertTrue(json.loads((self.root/'placement-baseline-before.json').read_text())['passed'])
        self.assertTrue(json.loads((self.root/'placement-evaluate-after.json').read_text())['passed'])

    def test_failed_qualification_or_quiet_never_starts_baseline(self):
        for stage in ('qualify','quiet'):
            with self.subTest(stage=stage),self.assertRaisesRegex(RuntimeError,stage+' failure'):self.fixture(fail=stage)
            self.assertNotIn('baseline',self.events);self.assertFalse((self.root/'baseline-result.json').exists())

    def test_baseline_failure_never_retries_or_evaluates(self):
        with self.assertRaisesRegex(RuntimeError,'baseline failed'):self.fixture(fail='baseline')
        self.assertEqual(self.events,['qualify_and_64controls','global_h_t_quiet','baseline','posthost'])


class NewPathRSSNative(rss.NativeTests):
    def setUp(self):
        if ROOT is None:self.skipTest('Actual split-path Linux integration requires --root')
        rss.ROOT=ROOT
        self.config=json.loads((ROOT/'placement-config.json').read_text())
        self.module=load(ROOT/'optimize-linux-split.py','actual_split_native')
        self.assertEqual(self.module._PLACEMENT_STARTUP_CPUS,[self.config['host_cpu']])
        self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.addCleanup(self.temp.cleanup);self.cwd=Path(self.temp.name)
        self.environment=self.module.runtime_environment()

    def test_actual_target_pid_mask_and_current_cpu(self):
        code='import ctypes,json,os; print(json.dumps({"pid":os.getpid(),"cpus":sorted(os.sched_getaffinity(0)),"cpu":ctypes.CDLL(None).sched_getcpu()}))'
        result=self.module.measure([sys.executable,'-c',code],self.cwd,self.environment,time.monotonic()+3,capture_version=True)
        output=json.loads(result['version_output']);self.assertEqual(output['pid'],result['rss_receipt']['target_pid'])
        self.assertEqual(output['cpus'],[self.config['target_cpu']]);self.assertEqual(output['cpu'],self.config['target_cpu'])
        self.assertEqual(result['placement_receipt']['cpu'],self.config['target_cpu'])

    def test_already_exited_new_supervisor_and_private_evidence_fd_closed(self):
        wait=load(ROOT/'timing_wait.py','actual_new_supervisor_wait')
        before=len(list(Path('/proc/self/fd').iterdir()))
        with tempfile.TemporaryFile() as reportfd:
            target=str(ROOT/'rss-fixture')
            child=subprocess.Popen([str(ROOT/'placement-supervisor'),str(reportfd.fileno()),target,target,'exit','127'],
                start_new_session=True,pass_fds=(reportfd.fileno(),),env=self.environment)
            reaped=False
            try:
                stop=time.monotonic()+3;terminated=None
                while time.monotonic()<stop:
                    terminated=os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOWAIT|os.WNOHANG)
                    if terminated is not None and terminated.si_pid==child.pid:break
                    time.sleep(0.001)
                self.assertIsNotNone(terminated)
                self.assertEqual(terminated.si_pid,child.pid)
                self.assertEqual(terminated.si_status,0)
                code,usage,expired=wait.wait_for_child(child,time.monotonic()+3,lambda pid:os.killpg(pid,signal.SIGKILL))
                child.returncode=code;reaped=True;self.assertEqual(code,0);self.assertFalse(expired)
                reportfd.seek(0);payload=reportfd.read(673)
                record,placement=adapter.decode_placement(payload,child.pid,self.config['target_cpu'])
                self.assertEqual(record['exit_code'],127);self.assertEqual(placement['supervisor_pid'],child.pid)
            finally:
                if not reaped:
                    with contextlib.suppress(ProcessLookupError):os.killpg(child.pid,signal.SIGKILL)
                    with contextlib.suppress(ChildProcessError):child.wait(timeout=3)
        self.assertEqual(before,len(list(Path('/proc/self/fd').iterdir())))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path);args=parser.parse_args();ROOT=args.root.resolve() if args.root else None
    suite=unittest.TestSuite()
    for case in (LegacyRSSProtocol,timing.WaitTests,PlacementTests,ReadinessTests,ControlTests,RunnerTests,NewPathRSSNative):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    result=unittest.TextTestRunner(verbosity=2).run(suite);raise SystemExit(0 if result.wasSuccessful() else 1)
