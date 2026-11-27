"""Separate neutral-output diagnostic builds; production construction is unchanged."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

FILES=('neutral_probe.c','output_build.py','output_controls.py','output_tests.py','run_output.py')


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def build(root,production):
    package=Path(__file__).parent;previous=load(package/'python_first_build.py','_same_diagnostic_builder');manifest=previous.build(root,production)
    for name in FILES:shutil.copyfile(package/name,root/name)
    cc=shutil.which('cc');common=[cc,'-O2','-std=c11','-Wall','-Wextra','-Werror'];records=[]
    def command(argv):
        try:
            result=subprocess.run(argv,capture_output=True,text=True,timeout=45)
            record={'argv':argv,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
        except subprocess.TimeoutExpired as error:
            def text(v):return v.decode(errors='replace') if isinstance(v,bytes) else (v or '')
            records.append({'argv':argv,'timeout':True,'stdout':text(error.stdout),'stderr':text(error.stderr)})
            (root/'output-build.json').write_text(json.dumps(records,indent=2)+'\n');raise RuntimeError('Neutral probe build timed out') from error
        records.append(record);(root/'output-build.json').write_text(json.dumps(records,indent=2)+'\n')
        if result.returncode:raise RuntimeError('Neutral probe build/inspection failed')
        return result.stdout
    identities={}
    for mode in ('static','dynamic'):
        binary=root/('neutral-'+mode)
        command(common+(['-static'] if mode=='static' else [])+['-o',str(binary),str(root/'neutral_probe.c')])
        identities[mode]=previous.elf_identity(command(['readelf','-h',str(binary)]),command(['readelf','-l',str(binary)]),command(['readelf','-d',str(binary)]),mode=='static')
    interpreter=Path(identities['dynamic']['interpreter'])
    previous.elf_identity(command(['readelf','-h',str(interpreter)]),'','',True)
    identities['dynamic'].update(interpreter_realpath=str(interpreter.resolve()),interpreter_sha256=previous.sha(interpreter))
    files=dict(manifest['diagnostic_files']);files.update({name:previous.sha(root/name) for name in FILES+('neutral-static','neutral-dynamic','output-build.json','python-first-setup.json')})
    new=dict(manifest,diagnostic_files=files,neutral_elf=identities,previous_manifest_sha256=previous.sha(root/'python-first-setup.json'))
    (root/'output-series-setup.json').write_text(json.dumps(new,indent=2)+'\n');return new
