"""Build a static entry shim around the exact reviewed RSS C body."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

NEW_FILES=('placement_shim.c','placement_config.py','placement_adapter.py','placement_control.c',
           'placement_controls.py','placement_build.py','placement_tests.py','placement_readiness.py','run_small.py')


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def generate(oldbuild,rssbuild,source,binding,config):
    derived=oldbuild.generate(rssbuild,source,binding,'pidfd')
    old='Path(__file__).with_name("timing_adapter.py")'
    if derived.count(old)!=1:raise ValueError('Unexpected prior timing adapter hook')
    derived=derived.replace(old,'Path(__file__).with_name("placement_adapter.py")')
    marker="_TIMING_MODE = 'pidfd'\n"
    addition=('_PLACEMENT = '+repr(config)+'\n_PLACEMENT_MODE = \'split\'\n'
              '_PLACEMENT_STARTUP_CPUS = sorted(os.sched_getaffinity(0))\n'
              'require(_PLACEMENT_STARTUP_CPUS == [_PLACEMENT["host_cpu"]], "Production evaluator must start on H")\n'+marker)
    derived=derived.replace(marker,addition,1)
    memory=rssbuild.MEMORY+'; split evaluator CPU '+str(config['host_cpu'])+' and supervisor/target CPU '+str(config['target_cpu'])
    if derived.count(rssbuild.MEMORY)!=2:raise ValueError('Unexpected RSS metadata')
    derived=derived.replace(rssbuild.MEMORY,memory)
    derived=derived.replace('"environment": environment,', '"environment": environment, "cpu_placement": dict(_PLACEMENT, startup_cpus=_PLACEMENT_STARTUP_CPUS),', 1)
    return derived


def build(root,config):
    package=Path(__file__).parent
    previous=load(package/'timing_build.py','_preserved_timing_builder')
    previous.build(root)
    cpuconfig=load(package/'placement_config.py','_placement_choices')
    if config!=cpuconfig.choose(config['initial_allowed_cpus']):raise RuntimeError('CPU choices not deterministic')
    for name in NEW_FILES:shutil.copyfile(package/name,root/name)
    (root/'placement-config.json').write_text(json.dumps(config,indent=2)+'\n')
    cc=shutil.which('cc')
    common=[cc,'-O2','-std=c11','-Wall','-Wextra','-Werror']
    commands=[common+['-Dmain=rss_original_main','-c',str(root/'rss_supervisor.c'),'-o',str(root/'placement-original.o')],
              common+['-static','-DTARGET_CPU='+str(config['target_cpu']),'-o',str(root/'placement-supervisor'),str(root/'placement_shim.c'),str(root/'placement-original.o')],
              common+['-static','-o',str(root/'placement-control'),str(root/'placement_control.c')],
              ['readelf','-l',str(root/'placement-supervisor')]]
    records=[]
    for command in commands:
        try:
            result=subprocess.run(command,capture_output=True,text=True,timeout=45)
            record={'argv':command,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
        except subprocess.TimeoutExpired as error:
            def decoded(value):return value.decode(errors='replace') if isinstance(value,bytes) else (value or '')
            records.append({'argv':command,'timeout':True,'stdout':decoded(error.stdout),'stderr':decoded(error.stderr)})
            (root/'placement-build.json').write_text(json.dumps(records,indent=2)+'\n')
            raise RuntimeError('Placement helper build timed out') from error
        records.append(record);(root/'placement-build.json').write_text(json.dumps(records,indent=2)+'\n')
        if result.returncode:raise RuntimeError('Placement helper build failed')
    if 'INTERP' in records[-1]['stdout']:raise RuntimeError('Placement supervisor must be static')
    prior=json.loads((root/'timing-provenance.json').read_text())
    names=tuple(prior['binding'])+('optimize-linux-pidfd.py','optimize-linux-timing-polling.py','timing-provenance.json',
        'placement-original.o','placement-supervisor','placement-control','placement-config.json','placement-build.json')+NEW_FILES
    rssbuild=load(root/'rss_build.py','_original_rss_generator')
    binding={name:rssbuild.sha(root/name) for name in names}
    derived=root/'optimize-linux-split.py'
    derived.write_text(generate(previous,rssbuild,(root/'optimize.py').read_text(),binding,config))
    provenance={'production_placement':'split','production_filename':derived.name,'derivative_sha256':rssbuild.sha(derived),
                'cpu_choices':config,'binding':binding,'previous_timing_provenance_sha256':rssbuild.sha(root/'timing-provenance.json')}
    (root/'placement-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    return derived
