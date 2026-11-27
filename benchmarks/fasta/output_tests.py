"""Focused mocked neutral-output tests; no native control or FASTA execution."""
import ast
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE=Path(__file__).parent
CONFIG={'host_cpu':2,'target_cpu':3}


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


controls=load(HERE/'output_controls.py','neutral_controls_test')
old=load(HERE/'accounting_tests.py','original_accounting_fixture')
prior=load(HERE/'python_first_controls.py','original_predecessor_validation')
accounting=load(HERE/'accounting_adapter.py','unchanged_accounting')
placement=load(HERE/'placement_controls.py','original_placement_validation')


class OutputTests(unittest.TestCase):
    def observation(self,root,row,cwd,start=1):
        role=row['role'];observed=old.PairTests.observation(root,role,'sleeping',start)
        account=observed['accounting_receipt'];account['target_rss_kib']=4096
        raw=old.placement.report()+json.dumps(account).encode()+b'\n'
        with patch.object(accounting.os,'sysconf',return_value=4096):rss,mask,account=accounting.decode_accounting(raw,100,3)
        observed.update(argv=controls.argv(root,row),cwd=str(cwd),private_report_raw=raw.decode(),supervisor_pid=100,rss_receipt=rss,
            placement_receipt=mask,accounting_receipt=account,wait4_ru_maxrss=4096,peak_rss_bytes=4096*1024)
        if role=='predecessor':
            output=json.loads(observed['version_output']);output.update(mode='sleeping',budget_us=(500,250000)[row['duration']]);observed['effects']=None
        else:
            size=controls.SIZES[row['volume']];data=b'Z'*size
            fd=os.open(cwd/'report.bin',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            try:os.write(fd,data)
            finally:os.close(fd)
            snapshot=lambda cpu,count:{'user_sec':0,'user_usec':cpu,'system_sec':0,'system_usec':0,**{key:count for key in accounting.COUNTERS}}
            output={'pid':101,'cpu_count':1,'cpu':3,'current_cpu':3,'requested_bytes':size,'written_bytes':size,
                'entry_wall_ns':account['fork_monotonic_ns']+10000,'finish_wall_ns':account['reap_monotonic_ns']-10000,
                'U':snapshot(100,1),'V':snapshot(300,3)}
            observed['effects']=[['report.bin','file',0o600,size,controls.HASHES[row['volume']]]]
        text=json.dumps(output);data=(text+'\n').encode();observed.update(version_output=text,stdout={'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        return observed

    def test_schedule_full_balance_and_factorial_scaling(self):
        rows=controls.schedule();self.assertEqual(len(rows),64)
        self.assertTrue(all(value==1 for value in Counter((row['condition'],row['position']) for row in rows).values()))
        transitions=Counter()
        for block in range(8):
            row=[value['condition'] for value in rows if value['block']==block];transitions.update(zip(row,row[1:]))
        self.assertEqual(len(transitions),56);self.assertTrue(all(value==1 for value in transitions.values()))
        pairs=[]
        for row in rows:
            d,k,w=row['duration'],row['dynamic'],row['volume'];value=10*d+3*k+w+2*d*k
            pairs.append(dict(row,probe={'metrics':{str(i):value for i in range(11)}},actual_gap_seconds=.002,
                predecessor={'body_wall_seconds':1,'body_cpu_seconds':0,'observation':{'elapsed_seconds':1,'peak_rss_bytes':None,'accounting_receipt':{}}}))
        summary=controls.summarize(pairs);self.assertEqual(len(summary['factorial_effects']),7)
        for effect,expected in (('D',11),('K',4),('W',1),('DxK',2),('DxW',0),('KxW',0),('DxKxW',0)):
            self.assertEqual(summary['factorial_effects'][effect]['0']['raw_by_block'],[expected]*8)

    def probe_fixture(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);root=Path(temporary.name);cwd=root/'probe';cwd.mkdir()
        row=dict(controls.schedule()[0],role='probe',cwd=str(cwd),parent_before=[2],parent_after=[2],deadline_monotonic=4)
        row['observation']=self.observation(root,row,cwd);return root,row

    @staticmethod
    def set_output(row,output):
        text=json.dumps(output);raw=(text+'\n').encode();row['observation'].update(version_output=text,stdout={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})

    def checked(self,root,row):
        with patch.object(accounting.os,'sysconf',return_value=4096):controls.validate(row,root,CONFIG,accounting,prior,placement)

    def test_signed_cpu_residuals_and_all_raw_file_effects(self):
        root,row=self.probe_fixture();output=json.loads(row['observation']['version_output']);output['U']['user_usec']=502;output['V']['user_usec']=501
        self.set_output(row,output);self.checked(root,row)
        self.assertEqual(row['signed_cpu_components_us'],{'cpu_at_entry':502,'cpu_in_body':-1,'cpu_after_body':-1})
        self.assertEqual(len(row['metrics']),11);self.assertAlmostEqual(sum(row['metrics'][key] for key in ('preparation','launch','startup','body','after_body','finish')),row['metrics']['full_elapsed'])
        (Path(row['cwd'])/'report.bin').write_bytes(b'X'*544)
        with self.assertRaises(ValueError):self.checked(root,row)

    def test_wrong_status_cpu_pid_bytes_clocks_counters_effects_refuse(self):
        for field,value in (('pid',102),('cpu',2),('written_bytes',0),('entry_wall_ns',0),('U',{'bad':1})):
            root,row=self.probe_fixture();output=json.loads(row['observation']['version_output']);output[field]=value;self.set_output(row,output)
            with self.assertRaises(ValueError):self.checked(root,row)
        root,row=self.probe_fixture();row['observation']['exit_code']=2
        with self.assertRaises(ValueError):self.checked(root,row)
        root,row=self.probe_fixture();row['observation']['effects']=[]
        with self.assertRaises(ValueError):self.checked(root,row)
        root,row=self.probe_fixture();output=json.loads(row['observation']['version_output']);output['V']['minor_faults']=0;self.set_output(row,output)
        with self.assertRaises(ValueError):self.checked(root,row)

    def fixture(self,failure=None):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);root=Path(temporary.name);clock=[1.0];events=[];last_end=[0]
        production=root/'optimize-linux-accounting.py';production.write_text('# production fixture\n');digest=controls.sha(production);bindings={str(i):'0'*64 for i in range(55)}
        (root/'accounting-provenance.json').write_text(json.dumps({'production_filename':production.name,'derivative_sha256':digest,'binding':bindings}))
        (root/'output-series-setup.json').write_text(json.dumps({'production_evaluator':production.name,'production_evaluator_sha256':digest,'production_bindings':bindings,'diagnostic_files':{}}))
        plans=iter(controls.schedule());active=[None]
        def save(path,value):events.append('save');path.write_text(json.dumps(value))
        def measure(argv,cwd,environment,deadline,**kwargs):
            role='predecessor' if Path(argv[0]).name=='crossover-predecessor' else 'probe';events.append('measure '+role)
            if role=='predecessor':active[0]=next(plans);self.assertEqual(len(list((root/'neutral-output').iterdir())),64)
            self.assertEqual(kwargs['record_effects'],role=='probe');self.assertTrue(kwargs['capture_version']);self.assertLessEqual(deadline,61)
            if role=='probe' and failure=='probe':raise RuntimeError('probe failed')
            row=dict(active[0],role=role);start=last_end[0]+.001 if role=='probe' and failure=='gap' else clock[0]
            observed=self.observation(root,row,cwd,start);clock[0]=observed['finished_monotonic']
            if role=='predecessor':last_end[0]=observed['finished_monotonic']
            if failure=='deadline':clock[0]=100
            return observed
        module=SimpleNamespace(save=save,measure=measure,runtime_environment=lambda:{})
        with patch.object(controls,'load',side_effect=[module,accounting,prior,placement,SimpleNamespace(require_parent=lambda cpu:[cpu])]), \
             patch.object(controls.time,'monotonic',side_effect=lambda:clock[0]),patch.object(controls.time,'sleep',side_effect=lambda delay:clock.__setitem__(0,clock[0]+delay)), \
             patch.object(controls.os,'sched_getaffinity',return_value={2},create=True),patch.object(accounting.os,'sysconf',return_value=4096):
            if failure:
                with self.assertRaisesRegex(RuntimeError,'diagnostic failed'):controls.run(root,CONFIG)
            else:self.assertTrue(controls.run(root,CONFIG)['passed'])
        return json.loads((root/'output-controls.json').read_text()),events

    def test_all128_and_partial_gap_deadline_failures(self):
        result,events=self.fixture();self.assertEqual(result['completed_calls'],128);self.assertEqual(len(result['summary']['factorial_effects']),7)
        for index,event in enumerate(events):
            if event=='measure predecessor':self.assertEqual(events[index+1],'measure probe')
        for failure in ('probe','gap','deadline'):
            result,events=self.fixture(failure);self.assertFalse(result['passed']);self.assertEqual(len(result['pairs']),1);self.assertIn('functional_error',result['pairs'][0])

    def test_production_identity_refuses_before_any_import(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);production=root/'optimize-linux-accounting.py';production.write_text('# fixed production\n')
            digest=controls.sha(production);bindings={str(i):'0'*64 for i in range(55)}
            (root/'accounting-provenance.json').write_text(json.dumps({'production_filename':production.name,'derivative_sha256':digest,'binding':bindings}))
            manifest={'production_evaluator':production.name,'production_evaluator_sha256':digest,'production_bindings':bindings,'diagnostic_files':{}}
            for field,value in (('production_evaluator','optimize-linux-split.py'),('production_evaluator_sha256','0'*64),('production_bindings',{})):
                (root/'output-series-setup.json').write_text(json.dumps(dict(manifest,**{field:value})))
                with patch.object(controls,'load') as imported:
                    with self.assertRaisesRegex(RuntimeError,'before import'):controls.run(root,CONFIG)
                imported.assert_not_called()

    def test_source_first_actions_and_runner_main_unchanged(self):
        source=(HERE/'neutral_probe.c').read_text();main=source[source.index('int main('):]
        self.assertLess(main.index('clock_gettime('),main.index('getrusage('));self.assertLess(main.index('getrusage('),main.index('strtol('))
        self.assertIn('O_CREAT|O_EXCL|O_WRONLY,0600',source);self.assertIn('char buffer[8192];memset(buffer,0x5A',source)
        self.assertIn('if(close(fd))return 3;\n    if(getrusage(',source)
        for size,digest in zip(controls.SIZES,controls.HASHES):self.assertEqual(hashlib.sha256(b'Z'*size).hexdigest(),digest)
        def main_ast(path):return next(node for node in ast.parse(path.read_text()).body if isinstance(node,ast.FunctionDef) and node.name=='main')
        self.assertEqual(ast.dump(main_ast(HERE/'run_accounting.py'),include_attributes=False),ast.dump(main_ast(HERE/'run_output.py'),include_attributes=False))
        text=(HERE/'run_output.py').read_text();self.assertIn("str(root/'accounting_tests.py')",text);self.assertIn('diagnostics.qualify(root)',text);self.assertNotIn("load(root/'host_controls.py'",text)


if __name__=='__main__':unittest.main(verbosity=2)
