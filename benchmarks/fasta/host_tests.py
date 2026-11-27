"""Mocked location/identity/routing fixtures; no live taskset or native controls."""
import ast
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE=Path(__file__).parent
CONFIG={'host_cpu':2,'target_cpu':3}


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


controls=load(HERE/'host_controls.py','host_controls_test')
prior=load(HERE/'python_first_tests.py','preserved_diagnostic_tests')
accounting=load(HERE/'accounting_adapter.py','same_accounting')
placement=load(HERE/'placement_controls.py','same_probe')


class HostTests(unittest.TestCase):
    def observation(self,root,role,condition,tool,start=1):
        mode,placement_role=controls.CONDITIONS[condition]
        result=prior.CrossoverTests.observation(self,root,role,2 if mode=='busy' else 3,start)
        row={'role':role,'taskset_path':str(tool),'mode':mode,'placement_role':placement_role}
        result['argv']=controls.argv(root,row,CONFIG);result['supervisor_pid']=100
        output=json.loads(result['version_output'])
        if role=='predecessor':output.update(cpu=CONFIG[placement_role],current_cpu=CONFIG[placement_role])
        text=json.dumps(output);raw=(text+'\n').encode();result.update(version_output=text,stdout={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        return result

    def test_schedule_positions_transitions_and_fixed_argv(self):
        rows=controls.schedule();self.assertEqual(len(rows),32)
        self.assertEqual(Counter((row['condition'],row['position']) for row in rows),Counter({(c,p):2 for c in range(4) for p in range(4)}))
        transitions=Counter()
        for block in range(8):
            row=[p['condition'] for p in rows if p['block']==block];transitions.update(zip(row,row[1:]))
        self.assertEqual(len(transitions),12);self.assertTrue(all(count==2 for count in transitions.values()))
        for row in rows:
            pred=dict(row,role='predecessor',taskset_path='/absolute/taskset')
            args=controls.argv(Path('/root'),pred,CONFIG)
            self.assertEqual(args,['/absolute/taskset','--cpu-list',str(CONFIG[row['placement_role']]),'/root/crossover-predecessor',row['mode'],'250000'])
            self.assertEqual(controls.argv(Path('/root'),dict(pred,role='probe'),CONFIG),['/root/dynamic-probe','0'])

    def test_supervisor_t_and_actual_body_h_or_t_independent(self):
        for condition in range(4):
            row=dict(controls.schedule()[condition],role='predecessor',taskset_path='/tool',parent_before=[2],parent_after=[2],deadline_monotonic=4)
            # Select the condition explicitly, independent of first-block order.
            row.update(condition=condition,mode=controls.CONDITIONS[condition][0],placement_role=controls.CONDITIONS[condition][1])
            row['observation']=self.observation(Path('/root'),'predecessor',condition,Path('/tool'))
            controls.validate_call(row,CONFIG,Path('/root'),accounting,placement)
            wrong=dict(row,observation=dict(row['observation'],placement_receipt={'cpu':2,'cpu_count':1,'supervisor_pid':100}))
            with self.assertRaisesRegex(ValueError,'Supervisor'):controls.validate_call(wrong,CONFIG,Path('/root'),accounting,placement)
            for field,value in (('cpu',99),('current_cpu',99),('target_pid',102)):
                observed=dict(row['observation']);output=json.loads(observed['version_output']);output[field]=value
                text=json.dumps(output);raw=(text+'\n').encode();observed.update(version_output=text,stdout={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
                with self.assertRaises(ValueError):controls.validate_call(dict(row,observation=observed),CONFIG,Path('/root'),accounting,placement)
        probe=dict(row,role='probe',observation=self.observation(Path('/root'),'probe',0,Path('/tool')))
        controls.validate_call(probe,CONFIG,Path('/root'),accounting,placement)

    def fixture(self,failure=None):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);root=Path(temporary.name)
        production=root/'optimize-linux-accounting.py';production.write_text('# production fixture\n');digest=controls.sha(production)
        bindings={'binding-'+str(i):'0'*64 for i in range(55)}
        provenance={'production_filename':production.name,'derivative_sha256':digest,'binding':bindings}
        (root/'accounting-provenance.json').write_text(json.dumps(provenance))
        tool=root/'taskset';tool.write_text('mock util-linux');tool_digest=controls.sha(tool)
        manifest={'production_evaluator':production.name,'production_evaluator_sha256':digest,'production_bindings':bindings,'diagnostic_files':{},'taskset':{'path':str(tool),'sha256':tool_digest}}
        if failure=='identity':manifest['production_evaluator']='optimize-linux-split.py'
        if failure=='digest':tool.write_text('modified')
        (root/'host-series-setup.json').write_text(json.dumps(manifest))
        clock=[1.0];events=[];condition=[0];pred_end=[0]
        def save(path,value):events.append('save');path.write_text(json.dumps(value))
        def measure(args,*rest,**kwargs):
            role='probe' if Path(args[0]).name=='dynamic-probe' else 'predecessor';events.append('measure '+role)
            if role=='predecessor':condition[0]=controls.CONDITIONS.index((args[4],'host_cpu' if args[2]=='2' else 'target_cpu'))
            if role=='probe' and failure=='probe':raise RuntimeError('probe failed')
            start=pred_end[0]+0.001 if role=='probe' and failure=='gap' else clock[0]
            observed=self.observation(root,role,condition[0],tool,start);clock[0]=observed['finished_monotonic']
            if role=='predecessor':pred_end[0]=observed['finished_monotonic']
            if failure=='deadline':clock[0]=100
            return observed
        module=SimpleNamespace(save=save,measure=measure,runtime_environment=lambda:{})
        affinity=SimpleNamespace(require_parent=lambda cpu:[cpu])
        original=controls.validate_call
        def checked(*args):events.append('validate');return original(*args)
        with patch.object(controls,'load',side_effect=[module,accounting,placement,affinity]) as imported,patch.object(controls,'validate_call',side_effect=checked), \
             patch.object(controls.time,'monotonic',side_effect=lambda:clock[0]),patch.object(controls.time,'sleep',side_effect=lambda delay:clock.__setitem__(0,clock[0]+delay)), \
             patch.object(controls.os,'sched_getaffinity',return_value={2},create=True):
            if failure:
                with self.assertRaises(RuntimeError):controls.run(root,CONFIG)
            else:self.assertTrue(controls.run(root,CONFIG)['passed'])
            if failure=='identity':imported.assert_not_called()
        path=root/'host-controls.json'
        return json.loads(path.read_text()) if path.exists() else None,events

    def test_all64_deferred_validation_five_contrasts(self):
        result,events=self.fixture();self.assertEqual(result['completed_calls'],64);self.assertEqual(len(result['pairs']),32)
        self.assertEqual(len(result['summary']['contrasts']),5);self.assertFalse(result['performance_selection'])
        for index,event in enumerate(events):
            if event=='measure predecessor':self.assertEqual(events[index+1],'measure probe')

    def test_partial_gap_deadline_tool_digest_and_preimport_failures(self):
        for failure in ('probe','gap','deadline','identity','digest'):
            result,events=self.fixture(failure)
            if failure in ('identity','digest'):self.assertFalse(any(event.startswith('measure') for event in events))
            else:
                self.assertFalse(result['passed']);self.assertEqual(len(result['pairs']),1);self.assertIn('functional_error',result['pairs'][0])

    def test_runner_main_and_production_setup_unchanged(self):
        old=ast.parse((HERE/'run_python_first.py').read_text());new=ast.parse((HERE/'run_host.py').read_text())
        def main(tree):return next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='main')
        self.assertEqual(ast.dump(main(old),include_attributes=False),ast.dump(main(new),include_attributes=False))
        text=(HERE/'run_host.py').read_text();self.assertIn("str(root/'accounting_tests.py')",text);self.assertIn('diagnostics.qualify(root)',text)
        self.assertNotIn("load(root/'python_first_controls.py'",text)
        self.assertIn("load(HERE/'host_build.py'",text)


if __name__=='__main__':unittest.main(verbosity=2)
