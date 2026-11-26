"""Prior RSS tests routed through production, plus pidfd/timestamp/control tests."""
import argparse
import ast
import base64
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zlib

HERE=Path(__file__).parent
ROOT=None


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


wait=load(HERE/'timing_wait.py','wait_test')
adapter=load(HERE/'timing_adapter.py','timing_adapter_test')
controls=load(HERE/'timing_controls.py','controls_test')
prior=load(HERE/'test_rss.py','unchanged_rss_tests')
# All nine prior ProtocolTests now exercise the production timing adapter.
prior.adapter=adapter


class ProductionRSSNative(prior.NativeTests):
    def setUp(self):
        if ROOT is None:
            self.skipTest('Production Linux integration requires --root')
        prior.ROOT=ROOT
        self.module=load(ROOT/'optimize-linux-pidfd.py','production_rss_native')
        self.assertEqual(self.module._TIMING_MODE,'pidfd')
        self.temp=tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.cwd=Path(self.temp.name)
        self.environment=self.module.runtime_environment()


class WaitTests(unittest.TestCase):
    def invoke(self,events,waits,clock=0,deadline=10,register_error=False):
        poller=Mock()
        poller.poll.side_effect=events
        if register_error:poller.register.side_effect=RuntimeError('register failed')
        kills=[]
        with patch.object(wait.os,'pidfd_open',return_value=42,create=True) as opened, \
             patch.object(wait.select,'poll',return_value=poller,create=True), \
             patch.object(wait.os,'wait4',side_effect=waits) as reaped, \
             patch.object(wait.os,'close') as closed, \
             patch.object(wait.time,'monotonic',return_value=clock):
            try:
                result=wait.wait_for_child(SimpleNamespace(pid=100),deadline,kills.append)
            finally:
                self.opened=opened;self.reaped=reaped;self.closed=closed;self.poller=poller;self.kills=kills
        return result

    def test_ready_fast_exit_codes_signal_and_exact_nonblocking_reap(self):
        for raw,expected in ((0,0),(127<<8,127),(signal.SIGTERM,-signal.SIGTERM)):
            self.assertEqual(self.invoke([[(42,select.POLLIN)]],[(100,raw,SimpleNamespace(ru_maxrss=12))]),(expected,12,False))
            self.reaped.assert_called_once_with(100,os.WNOHANG)
            self.closed.assert_called_once_with(42)
            self.assertFalse(self.kills)

    def test_deadline_expiry_checks_once_then_kills_and_reaps(self):
        self.assertEqual(self.invoke([],[(0,0,None),(100,signal.SIGKILL,SimpleNamespace(ru_maxrss=8))],clock=10),(-signal.SIGKILL,8,True))
        self.assertEqual(self.kills,[100]);self.assertEqual([c.args for c in self.reaped.call_args_list],[(100,os.WNOHANG),(100,0)])
        self.closed.assert_called_once_with(42)
        self.poller.poll.assert_not_called()

    def test_expired_but_already_exited_reaps_without_kill(self):
        self.assertEqual(self.invoke([],[(100,0,SimpleNamespace(ru_maxrss=7))],clock=10),(0,7,False))
        self.assertFalse(self.kills);self.closed.assert_called_once_with(42)

    def test_invalid_event_and_spurious_readiness_fail_closed_and_close(self):
        for event in ([(42,select.POLLNVAL)],[(41,select.POLLIN)],[(42,select.POLLIN),(42,select.POLLIN)]):
            with self.subTest(event=event),self.assertRaisesRegex(RuntimeError,'Invalid pidfd'):
                self.invoke([event],[])
            self.closed.assert_called_once_with(42);self.reaped.assert_not_called()
        with self.assertRaisesRegex(RuntimeError,'Spurious'):
            self.invoke([[(42,select.POLLIN)]],[(0,0,None)])
        self.closed.assert_called_once_with(42)

    def test_interrupted_notification_and_wait_are_retried_without_sleep(self):
        self.assertEqual(self.invoke([InterruptedError(),[(42,select.POLLIN)]],[InterruptedError(),(100,0,SimpleNamespace(ru_maxrss=2))]),(0,2,False))
        self.assertEqual(self.poller.poll.call_count,2);self.closed.assert_called_once_with(42)

    def test_rounded_poll_deadline_is_rechecked_before_reap(self):
        poller=Mock();poller.poll.return_value=[(42,select.POLLIN)]
        with patch.object(wait.os,'pidfd_open',return_value=42,create=True), \
             patch.object(wait.select,'poll',return_value=poller,create=True), \
             patch.object(wait.time,'monotonic',side_effect=[9.9999,10]), \
             patch.object(wait.os,'wait4',return_value=(100,127<<8,SimpleNamespace(ru_maxrss=1))) as reap, \
             patch.object(wait.os,'close') as close:
            result=wait.wait_for_child(SimpleNamespace(pid=100),10,Mock())
        self.assertEqual(result,(127,1,False));poller.poll.assert_called_once_with(1)
        reap.assert_called_once_with(100,os.WNOHANG);close.assert_called_once_with(42)

    def test_missing_support_and_registration_failure_close(self):
        with patch.object(wait.os,'pidfd_open',None,create=True):
            with self.assertRaisesRegex(RuntimeError,'required'):
                wait.wait_for_child(SimpleNamespace(pid=100),10,lambda pid:None)
        with self.assertRaisesRegex(RuntimeError,'register failed'):
            self.invoke([],[],register_error=True)
        self.closed.assert_called_once_with(42)

    def test_open_failure_has_no_fallback(self):
        with patch.object(wait.os,'pidfd_open',side_effect=OSError('unsupported'),create=True),patch.object(wait.os,'wait4') as reap:
            with self.assertRaises(OSError):wait.wait_for_child(SimpleNamespace(pid=100),10,lambda pid:None)
        reap.assert_not_called()


class TimingTests(unittest.TestCase):
    def test_exact_clock_endpoints_and_mode_with_unchanged_rss_decode(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);work=root/'work';work.mkdir()
            module={'__file__':str(root/'derived.py'),'_TIMING_MODE':'pidfd','require':lambda value,message:None,
                    'wait_for_child':lambda child,deadline:(0,123,False),'kill_group':lambda pid:None,
                    'process_group_exists':lambda pid:False,'stream_signature':lambda path:{'size':path.stat().st_size},
                    'tree':lambda path:[],'save':lambda path,value:None,'Uncertain':RuntimeError}
            def popen(argv,**kwargs):
                os.write(kwargs['pass_fds'][0],prior.receipt())
                return SimpleNamespace(pid=100,returncode=None)
            with patch.object(adapter.subprocess,'Popen',side_effect=popen),patch.object(adapter.os,'sysconf',return_value=4096), \
                 patch.object(adapter.time,'monotonic',side_effect=[0,1,2,3]):
                row=adapter.measure(module,['/target'],work,{},10)
            self.assertEqual([row[k] for k in ('started_monotonic','popen_return_monotonic','finished_monotonic')],[1,2,3])
            self.assertEqual(row['elapsed_seconds'],row['finished_monotonic']-row['started_monotonic'])
            self.assertEqual(row['timing_mode'],'pidfd');self.assertEqual(row['peak_rss_bytes'],4096*1024)

    def test_interrupted_post_popen_clock_kills_and_reaps_original_group(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);work=root/'work';work.mkdir();killed=[]
            module={'__file__':str(root/'derived.py'),'require':lambda value,message:None,
                    'wait_for_child':Mock(),'kill_group':killed.append}
            with patch.object(adapter.subprocess,'Popen',return_value=SimpleNamespace(pid=100,returncode=None)), \
                 patch.object(adapter.time,'monotonic',side_effect=[0,1,KeyboardInterrupt()]), \
                 patch.object(adapter.os,'wait4',return_value=(100,0,SimpleNamespace())) as reap:
                with self.assertRaises(KeyboardInterrupt):adapter.measure(module,['/target'],work,{},10)
            self.assertEqual(killed,[100]);reap.assert_called_once_with(100,0)
            module['wait_for_child'].assert_not_called()

    def test_separate_derivative_identity_and_preserved_functions(self):
        builder=load(HERE/'rss_build.py','old_builder_test')
        newbuilder=load(HERE/'timing_build.py','new_builder_test')
        if ROOT:source=(ROOT/'optimize.py').read_text()
        else:
            bundle=json.loads((HERE/'bundle.json').read_text())
            source=zlib.decompress(base64.b64decode(bundle['files']['optimize.py']['zlib_base64'])).decode()
        binding={'timing_wait.py':'0'*64,'rss_adapter.py':'1'*64}
        pidfd=newbuilder.generate(builder,source,binding,'pidfd')
        polling=newbuilder.generate(builder,source,binding,'polling')
        self.assertNotEqual(hashlib.sha256(pidfd.encode()).hexdigest(),hashlib.sha256(polling.encode()).hexdigest())
        def functions(text):return {node.name:ast.dump(node,include_attributes=False) for node in ast.parse(text).body if isinstance(node,ast.FunctionDef)}
        old,new=functions(source),functions(pidfd)
        for name in set(old)-{'measure','wait_for_child','host_info','report_evaluation'}:
            self.assertEqual(old[name],new[name],name)
        self.assertIn('pidfd exit notification',pidfd)
        self.assertIn("_TIMING_MODE = 'pidfd'",pidfd)
        self.assertEqual(old['wait_for_child'],functions(polling)['wait_for_child'])

    def test_tampered_wait_refused_before_module_execution(self):
        builder=load(HERE/'rss_build.py','old_builder_binding_test')
        newbuilder=load(HERE/'timing_build.py','new_builder_binding_test')
        if ROOT:source=(ROOT/'optimize.py').read_text()
        else:
            bundle=json.loads((HERE/'bundle.json').read_text())
            source=zlib.decompress(base64.b64decode(bundle['files']['optimize.py']['zlib_base64'])).decode()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            dependency=root/'timing_wait.py';dependency.write_text('# reviewed wait module\n')
            binding={'timing_wait.py':hashlib.sha256(dependency.read_bytes()).hexdigest()}
            derivative=root/'derivative.py';derivative.write_text(newbuilder.generate(builder,source,binding,'pidfd'))
            marker=root/'must-not-execute'
            dependency.write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("executed")\n')
            with self.assertRaisesRegex(ValueError,'binding changed'):
                load(derivative,'tampered_wait_derivative')
            self.assertFalse(marker.exists())

    def test_adapter_copy_preserves_every_original_statement_except_raw_endpoints(self):
        original=ast.parse((HERE/'rss_adapter.py').read_text())
        changed=ast.parse((HERE/'timing_adapter.py').read_text())
        old=next(node for node in original.body if isinstance(node,ast.FunctionDef) and node.name=='measure')
        new=next(node for node in changed.body if isinstance(node,ast.FunctionDef) and node.name=='measure')
        duration=next(node for node in ast.walk(old) if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id=='duration')
        class Normalize(ast.NodeTransformer):
            def visit_Assign(self,node):
                if isinstance(node.targets[0],ast.Name):
                    if node.targets[0].id in ('popen_return','finished'):return None
                    if node.targets[0].id=='duration':return duration
                return self.generic_visit(node)
            def visit_Dict(self,node):
                pairs=[(key,value) for key,value in zip(node.keys,node.values) if not isinstance(key,ast.Constant) or key.value not in {'timing_mode','started_monotonic','popen_return_monotonic','finished_monotonic'}]
                node.keys=[key for key,value in pairs];node.values=[value for key,value in pairs]
                return self.generic_visit(node)
        normalized=Normalize().visit(new)
        self.assertEqual(ast.dump(old,include_attributes=False),ast.dump(normalized,include_attributes=False))

    def test_schedule_exact_counterbalance(self):
        schedule=controls.schedule();self.assertEqual(len(schedule),64)
        for block in range(16):
            group=[row for row in schedule if row['block']==block]
            self.assertEqual([row['duration_index'] for row in group],[0,0,1,1] if block%2==0 else [1,1,0,0])
            for duration in (0,1):
                pair=[row for row in group if row['duration_index']==duration]
                self.assertEqual(pair[0]['mode'],'polling' if (block//2+duration)%2==0 else 'pidfd')
        for duration in (0,1):
            for position in (0,1):
                rows=[row for row in schedule if row['duration_index']==duration and row['duration_position']==position and row['order_in_pair']==0]
                self.assertEqual(sum(row['mode']=='polling' for row in rows),4)
                self.assertEqual(sum(row['mode']=='pidfd' for row in rows),4)

    @staticmethod
    def observation(mode,dwell):
        finish=1001000000+dwell*1000
        end=finish/1e9+0.001
        return {'timing_mode':mode,'exit_code':0,'timeout':False,'residual_process_group':False,'instrument_error':None,
                'rss_receipt':{'floor_limited':True},'started_monotonic':1.0,'popen_return_monotonic':1.0005,
                'finished_monotonic':end,'elapsed_seconds':end-1.0,
                'version_output':json.dumps({'dwell_us':dwell,'entry_monotonic_ns':1001000000,'finish_monotonic_ns':finish})}

    def test_stage_order_and_exact_elapsed_no_assumed_popen_target_order(self):
        row={'mode':'pidfd','dwell_us':3500,'deadline_monotonic':4,'observation':self.observation('pidfd',3500)}
        row['output']=json.loads(row['observation']['version_output'])
        row['observation']['popen_return_monotonic']=1.004
        controls.validate(row)
        row['observation']['elapsed_seconds']+=0.1
        with self.assertRaisesRegex(ValueError,'timestamps'):controls.validate(row)

    def test_controls_keep_all_64_regardless_of_performance(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);calls=[]
            def loader(path,name):
                mode='pidfd' if 'pidfd' in path.name else 'polling'
                def measure(argv,*args,**kwargs):
                    calls.append((mode,int(argv[1]),args[2]));return self.observation(mode,int(argv[1]))
                return SimpleNamespace(save=lambda path,value:path.write_text(json.dumps(value)),measure=measure,runtime_environment=lambda:{})
            with patch.object(controls,'load',side_effect=loader),patch.object(controls.time,'monotonic',return_value=1):
                result=controls.run(root)
            self.assertTrue(result['passed']);self.assertEqual(len(calls),64)
            self.assertTrue(all(deadline==4 for mode,dwell,deadline in calls))
            self.assertEqual(len(list(root.glob('timing-control-*.json'))),64)
            self.assertEqual(len(result['summary']['groups']),4)

    def test_expired_control_series_retains_failure_before_any_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);measure=Mock()
            module=SimpleNamespace(save=lambda path,value:path.write_text(json.dumps(value)),measure=measure)
            with patch.object(controls,'load',return_value=module),patch.object(controls.time,'monotonic',side_effect=[0,61,61]):
                with self.assertRaisesRegex(RuntimeError,'functional failure'):controls.run(root)
            measure.assert_not_called()
            proof=json.loads((root/'timing-controls.json').read_text())
            self.assertFalse(proof['passed']);self.assertEqual(len(proof['records']),1)


class RunnerTests(unittest.TestCase):
    def fixture(self,fail=None):
        runner=load(HERE/'run_timing.py','runner_order_test')
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name).resolve()/'restored'
        events=[]
        def qualify(path):
            events.append('qualification_and_controls')
            if fail=='qualification':raise RuntimeError('qualification failure')
            return path/'optimize-linux-pidfd.py'
        def quiet(path,cpu):
            events.append('quiet')
            if fail=='quiet':raise RuntimeError('quiet failure')
        def command(argv,**kwargs):
            action=argv[2];events.append(action)
            self.assertEqual(Path(argv[1]).name,'optimize-linux-pidfd.py')
            self.assertEqual(argv[3:],[str(root/'contract.json'),'--runs',str(root/'runs'),'--expect-contract',runner.legacy.CONTRACT]+(['--candidate',str(root/'candidate')] if action=='evaluate' else []))
            self.assertEqual(kwargs['timeout'],240)
            if action=='evaluate':
                report=root/'fixture-report.json';report.write_text(json.dumps({'status':'fixture','parity':True,'workloads':[]}))
                kwargs['stdout'].write(json.dumps({'report_json':str(report)}))
            return SimpleNamespace(returncode=1 if fail==action else 0)
        with patch.object(sys,'argv',['run_timing.py','--destination',str(root)]),patch.object(runner.platform,'system',return_value='Linux'), \
             patch.object(runner.platform,'machine',return_value='aarch64'),patch.object(sys,'version_info',(3,15,0)), \
             patch.object(runner.legacy,'pin_cpu',return_value=3),patch.object(runner.legacy.frozen,'compiler_preflight'), \
             patch.object(runner.legacy.frozen,'capture'),patch.object(runner.legacy,'host_info'), \
             patch.object(runner,'qualify',side_effect=qualify),patch.object(runner.legacy,'check_readiness',side_effect=quiet), \
             patch.object(runner.os,'sched_getaffinity',return_value={3},create=True), \
             patch.object(runner.legacy,'snapshot',side_effect=lambda root,name:events.append('posthost')), \
             patch.object(runner.subprocess,'run',side_effect=command):
            try:runner.main()
            finally:self.events=events;self.root=root

    def test_single_pidfd_sequence_after_all_diagnostics_and_quiet(self):
        self.fixture()
        self.assertEqual(self.events,['qualification_and_controls','quiet','baseline','evaluate','posthost'])

    def test_qualification_or_quiet_failure_never_starts_baseline(self):
        for stage in ('qualification','quiet'):
            with self.subTest(stage=stage),self.assertRaisesRegex(RuntimeError,stage+' failure'):self.fixture(fail=stage)
            self.assertNotIn('baseline',self.events)
            self.assertFalse((self.root/'baseline-result.json').exists())

    def test_benchmark_failure_retains_posthost_and_never_retries(self):
        with self.assertRaisesRegex(RuntimeError,'baseline failed'):self.fixture(fail='baseline')
        self.assertEqual(self.events,['qualification_and_controls','quiet','baseline','posthost'])


class PidfdNativeTests(unittest.TestCase):
    def test_already_exited_same_pid_status_and_no_fd_leak(self):
        if ROOT is None:self.skipTest('Linux pidfd integration requires --root')
        before=len(list(Path('/proc/self/fd').iterdir()))
        child=subprocess.Popen([str(ROOT/'rss-fixture'),'exit','127'],start_new_session=True)
        reaped=False
        try:
            stop=time.monotonic()+3
            terminated=None
            while time.monotonic()<stop:
                terminated=os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOWAIT|os.WNOHANG)
                if terminated is not None and terminated.si_pid==child.pid:break
                time.sleep(0.001)
            self.assertIsNotNone(terminated,'Child did not exit within precondition bound')
            self.assertEqual(terminated.si_pid,child.pid)
            self.assertEqual(terminated.si_status,127)
            code,rss,expired=wait.wait_for_child(child,time.monotonic()+3,lambda pid:os.killpg(pid,signal.SIGKILL))
            child.returncode=code;reaped=True
            self.assertEqual(code,127);self.assertFalse(expired)
        finally:
            if not reaped:
                with contextlib.suppress(ProcessLookupError):os.killpg(child.pid,signal.SIGKILL)
                with contextlib.suppress(ChildProcessError):child.wait(timeout=3)
        self.assertEqual(before,len(list(Path('/proc/self/fd').iterdir())))



if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path)
    args=parser.parse_args();ROOT=args.root.resolve() if args.root else None
    suite=unittest.TestSuite()
    for case in (prior.ProtocolTests,ProductionRSSNative,WaitTests,TimingTests,RunnerTests,PidfdNativeTests):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
