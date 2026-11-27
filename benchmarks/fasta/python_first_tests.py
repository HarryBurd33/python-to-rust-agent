"""Mocked diagnostic checks only; original native suite runs unchanged in the VM."""
import ast
import base64
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

HERE=Path(__file__).parent
CONFIG={'host_cpu':2,'target_cpu':3}


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


build=load(HERE/'python_first_build.py','diagnostic_builder_test')
controls=load(HERE/'python_first_controls.py','crossover_controls_test')
old=load(HERE/'accounting_tests.py','preserved_accounting_fixtures')
accounting=load(HERE/'accounting_adapter.py','unchanged_accounting_test')
placement=load(HERE/'placement_controls.py','unchanged_probe_test')


class SetupTests(unittest.TestCase):
    def test_exact_imports_and_python_syntax_without_execution(self):
        if (HERE/'source/research_tools.py').exists():original=(HERE/'source/research_tools.py').read_text()
        else:
            bundle=json.loads((HERE/'bundle.json').read_text());original=zlib.decompress(base64.b64decode(bundle['files']['source/research_tools.py']['zlib_base64'])).decode()
        script=(HERE/'python_startup.py').read_text();build.verify_imports(original,script);compile(script,'python_startup.py','exec')
        with self.assertRaises(ValueError):build.verify_imports(original,script.replace('from pathlib import Path','import pathlib'))
        self.assertEqual(len(build.MODULES),15);self.assertEqual(build.MODULES,controls.MODULES)

    def test_exact_crossover_balance_and_transitions(self):
        rows=controls.schedule();self.assertEqual(len(rows),50)
        self.assertEqual(Counter(row['condition'] for row in rows),Counter({i:10 for i in range(5)}))
        self.assertTrue(all(value==2 for value in Counter((row['condition'],row['position']) for row in rows).values()))
        transitions=Counter()
        for block in range(10):
            conditions=[row['condition'] for row in rows if row['block']==block]
            self.assertEqual(set(conditions),set(range(5)));transitions.update(zip(conditions,conditions[1:]))
        self.assertEqual(len(transitions),20);self.assertTrue(all(value==2 for value in transitions.values()))
        self.assertEqual([row['condition'] for row in rows[:10]],[0,1,4,2,3,3,2,4,1,0])

    def test_budget_extension_and_echo_are_the_only_native_changes(self):
        original=(HERE/'predecessor_control.c').read_text()
        expected=original.replace('#include <stdio.h>','#include <stdio.h>\n#include <stdlib.h>').replace('argc!=2','argc!=3')
        expected=expected.replace('    cpu_set_t mask;','    char *end=NULL;long budget=strtol(argv[2],&end,10);\n    if (!end || *end || (budget!=500 && budget!=250000)) return 2;\n    cpu_set_t mask;')
        expected=expected.replace('deadline.tv_nsec+=250000000L','deadline.tv_nsec+=budget*1000L').replace(r'\"mode\":\"%s\",',r'\"mode\":\"%s\",\"budget_us\":%ld,').replace('        argv[1],ns(entered)','        argv[1],budget,ns(entered)')
        actual=(HERE/'crossover_predecessor.c').read_text();self.assertEqual(actual,expected)
        line=next(line for line in actual.splitlines() if line.strip().startswith('printf('))
        fmt=json.loads(line.strip()[len('printf('):-1]);example=json.loads(re.sub(r'%llu|%ld|%d','0',fmt.replace('%s','busy')))
        self.assertIn('budget_us',example);self.assertIn('self_rusage',example)

    def test_elf_machine_interpreter_and_libc_fail_closed(self):
        header='Class: ELF64\nMachine: AArch64\n';program='[Requesting program interpreter: /lib/ld-linux-aarch64.so.1]';dynamic='(NEEDED) Shared library: [libc.so.6]'
        self.assertEqual(build.elf_identity(header,program,dynamic)['needed_libc'],'libc.so.6')
        self.assertTrue(build.elf_identity(header,'','',True)['static'])
        for h,p,d,static in ((header.replace('AArch64','X86-64'),program,dynamic,False),(header,program.replace('/lib/','relative/'),dynamic,False),(header,program,'',False),(header,program,dynamic,True)):
            with self.assertRaises(ValueError):build.elf_identity(h,p,d,static)

    def test_runner_main_identical_and_prior_native_qualification_retained(self):
        before=ast.parse((HERE/'run_accounting.py').read_text());after=ast.parse((HERE/'run_python_first.py').read_text())
        def function(tree,name):return next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name==name)
        self.assertEqual(ast.dump(function(before,'main'),include_attributes=False),ast.dump(function(after,'main'),include_attributes=False))
        text=(HERE/'run_python_first.py').read_text()
        self.assertIn("str(root/'accounting_tests.py')",text);self.assertIn('diagnostics.qualify(root)',text)
        self.assertNotIn("load(root/'predecessor_controls.py'",text)
        self.assertLess(text.index('diagnostics.qualify(root)'),text.index('diagnostic_builder.build'))
        self.assertLess(text.index('diagnostic_builder.build'),text.index("load(root/'python_first_controls.py'"))


class CrossoverTests(unittest.TestCase):
    def observation(self,root,role,condition,start):
        row=old.PairTests.observation(root,role,'busy',start)
        row['argv']=controls.argv(root,condition,role)
        output=json.loads(row['version_output'])
        if role=='predecessor' and condition==4:
            output={'condition':'E','pid':101,'cpus':[3],'version':[3,15,0],'modules':list(controls.MODULES)}
        elif role=='predecessor':
            mode,budget=controls.CONDITIONS[condition];output['mode']=mode;output['budget_us']=budget
        text=json.dumps(output);raw=(text+'\n').encode();row.update(version_output=text,stdout={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        row['peak_rss_bytes']=None
        return row

    def test_e_identity_modules_and_native_budget_raw_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'python_startup.py').write_bytes((HERE/'python_startup.py').read_bytes())
            for condition in range(5):
                observed=self.observation(root,'predecessor',condition,1)
                mode,budget=controls.CONDITIONS[condition]
                row={'role':'predecessor','mode':mode,'condition':condition,'budget_us':budget,'parent_before':[2],'parent_after':[2],
                    'deadline_monotonic':4,'expected_script_sha256':controls.sha(root/'python_startup.py'),'observation':observed}
                controls.validate_call(row,CONFIG,root,accounting,placement)
                changed=dict(observed,stderr={'size':1,'sha256':'0'*64})
                with self.assertRaises(ValueError):controls.validate_call(dict(row,observation=changed),CONFIG,root,accounting,placement)
                output=json.loads(observed['version_output'])
                if condition==4:output['modules'].remove('pathlib')
                else:output['budget_us']=1
                text=json.dumps(output);raw=(text+'\n').encode();changed=dict(observed,version_output=text,stdout={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
                with self.assertRaises(ValueError):controls.validate_call(dict(row,observation=changed),CONFIG,root,accounting,placement)

    def run_fixture(self,failure=None):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);root=Path(temporary.name)
        (root/'python_startup.py').write_bytes((HERE/'python_startup.py').read_bytes())
        production=root/'optimize-linux-accounting.py';production.write_text('# mocked production fixture\n')
        digest=controls.sha(production);bindings={'fixture-'+str(index):'0'*64 for index in range(55)}
        (root/'accounting-provenance.json').write_text(json.dumps({'production_filename':production.name,'derivative_sha256':digest,'binding':bindings}))
        (root/'python-first-setup.json').write_text(json.dumps({'production_evaluator':production.name,'production_evaluator_sha256':digest,
            'production_bindings':bindings,'diagnostic_files':{'python_startup.py':controls.sha(root/'python_startup.py')}}))
        clock=[1.0];events=[];last=[0];pred_end=[0]
        def save(path,value):events.append('save');path.write_text(json.dumps(value))
        def measure(argv,*args,**kwargs):
            role='probe' if Path(argv[0]).name=='dynamic-probe' else 'predecessor'
            events.append('measure '+role)
            if role=='predecessor':last[0]=4 if Path(argv[0]).name==Path(sys.executable).name else controls.CONDITIONS.index((argv[1],int(argv[2])))
            if role=='probe' and failure=='probe':raise RuntimeError('failed probe')
            start=pred_end[0]+0.001 if role=='probe' and failure=='gap' else clock[0]
            observed=self.observation(root,role,last[0],start);clock[0]=observed['finished_monotonic']
            if role=='predecessor':pred_end[0]=observed['finished_monotonic']
            if failure=='deadline':clock[0]=100
            self.assertLessEqual(args[2],61);return observed
        module=SimpleNamespace(save=save,measure=measure,runtime_environment=lambda:{})
        affinity=SimpleNamespace(require_parent=lambda cpu:[cpu])
        validate=controls.validate_call
        def checked(*args):events.append('validate');return validate(*args)
        with patch.object(controls,'load',side_effect=[module,accounting,placement,affinity]),patch.object(controls,'validate_call',side_effect=checked), \
             patch.object(controls.time,'monotonic',side_effect=lambda:clock[0]),patch.object(controls.time,'sleep',side_effect=lambda value:clock.__setitem__(0,clock[0]+value)), \
             patch.object(controls.os,'sched_getaffinity',return_value={2},create=True):
            if failure:
                with self.assertRaisesRegex(RuntimeError,'functional failure'):controls.run(root,CONFIG)
            else:self.assertTrue(controls.run(root,CONFIG)['passed'])
        return json.loads((root/'python-first-controls.json').read_text()),events

    def test_wrong_selected_evaluator_or_digest_refused_before_any_import(self):
        for mutation in ('selected_path','setup_digest','actual_bytes','provenance_path','bindings'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);production=root/'optimize-linux-accounting.py';production.write_text('# production fixture\n')
                digest=controls.sha(production);bindings={'fixture-'+str(index):'0'*64 for index in range(55)}
                setup={'production_evaluator':production.name,'production_evaluator_sha256':digest,'production_bindings':bindings,'diagnostic_files':{}}
                provenance={'production_filename':production.name,'derivative_sha256':digest,'binding':dict(bindings)}
                if mutation=='selected_path':setup['production_evaluator']='optimize-linux-split.py'
                elif mutation=='setup_digest':setup['production_evaluator_sha256']='0'*64
                elif mutation=='actual_bytes':production.write_text('# replaced evaluator bytes\n')
                elif mutation=='provenance_path':provenance['production_filename']='optimize-linux-split.py'
                else:provenance['binding'].pop('fixture-0')
                (root/'python-first-setup.json').write_text(json.dumps(setup));(root/'accounting-provenance.json').write_text(json.dumps(provenance))
                with patch.object(controls,'load') as imported:
                    with self.assertRaisesRegex(RuntimeError,'before import'):controls.run(root,CONFIG)
                imported.assert_not_called()

    def test_all100_deferred_validation_and_contrasts(self):
        result,events=self.run_fixture();self.assertEqual(result['completed_calls'],100);self.assertEqual(len(result['pairs']),50)
        self.assertFalse(result['performance_selection']);self.assertEqual(len(result['summary']['contrasts']),8)
        for index,event in enumerate(events):
            if event=='measure predecessor':self.assertEqual(events[index+1],'measure probe')
        self.assertEqual(Counter(pair['condition'] for pair in result['pairs']),Counter({i:10 for i in range(5)}))

    def test_partial_gap_and_deadline_failures_preserved(self):
        for failure in ('probe','gap','deadline'):
            result,events=self.run_fixture(failure)
            self.assertFalse(result['passed']);self.assertEqual(len(result['pairs']),1)
            self.assertIn('observation',result['pairs'][0]['predecessor']);self.assertIn('functional_error',result['pairs'][0])


if __name__=='__main__':unittest.main(verbosity=2)
