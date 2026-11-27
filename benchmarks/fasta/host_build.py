"""Reuse diagnostic builds; add taskset identity and non-gating topology evidence."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

FILES=('host_build.py','host_controls.py','host_tests.py','run_host.py')


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def build(root,production):
    package=Path(__file__).parent
    previous=load(package/'python_first_build.py','_preserved_diagnostic_builder')
    manifest=previous.build(root,production)
    for name in FILES:shutil.copyfile(package/name,root/name)
    controls=load(root/'host_controls.py','_host_identity_checks')
    controls.verify_production(root,manifest)
    module=load(root/'optimize-linux-accounting.py','_same_production_environment');environment=module.runtime_environment()
    selected=shutil.which('taskset',path=environment.get('PATH',os.defpath))
    if selected is None:raise RuntimeError('Required taskset is unavailable')
    tool=Path(selected).resolve()
    if not tool.is_absolute() or not tool.is_file() or not os.access(tool,os.X_OK):raise RuntimeError('taskset must be an absolute executable')
    digest=previous.sha(tool)
    record={'path':str(tool),'sha256':digest,'argv':[str(tool),'--version']}
    try:
        result=subprocess.run(record['argv'],env=environment,cwd=root,capture_output=True,text=True,timeout=10)
        record.update(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr)
    except subprocess.TimeoutExpired as error:
        def text(value):return value.decode(errors='replace') if isinstance(value,bytes) else (value or '')
        record.update(timeout=True,stdout=text(error.stdout),stderr=text(error.stderr))
        (root/'taskset-identity.json').write_text(json.dumps(record,indent=2)+'\n');raise RuntimeError('taskset version timed out') from error
    (root/'taskset-identity.json').write_text(json.dumps(record,indent=2)+'\n')
    if result.returncode or not re.search(r'taskset from util-linux\s+\S+',result.stdout) or previous.sha(tool)!=digest:
        raise RuntimeError('taskset identity/version verification failed')
    topology={}
    config=json.loads((root/'placement-config.json').read_text())
    for role in ('host_cpu','target_cpu'):
        topology[role]={}
        for name in ('core_id','physical_package_id','thread_siblings_list'):
            path=Path('/sys/devices/system/cpu')/('cpu'+str(config[role]))/'topology'/name
            try:topology[role][name]={'value':path.read_text().strip(),'error':None}
            except OSError as error:topology[role][name]={'value':None,'error':str(error)}
    (root/'host-topology.json').write_text(json.dumps(topology,indent=2)+'\n')
    files=dict(manifest['diagnostic_files'])
    files.update({name:previous.sha(root/name) for name in FILES+('python-first-setup.json','taskset-identity.json','host-topology.json')})
    new=dict(manifest,diagnostic_files=files,taskset=record,previous_manifest_sha256=previous.sha(root/'python-first-setup.json'))
    (root/'host-series-setup.json').write_text(json.dumps(new,indent=2)+'\n')
    return new
