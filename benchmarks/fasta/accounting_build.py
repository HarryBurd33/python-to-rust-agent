"""Preserve prior instruments and bind new parent-only native accounting."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

NEW_FILES=('accounting_source.py','accounting_adapter.py','accounting_build.py','predecessor_control.c',
           'predecessor_controls.py','accounting_tests.py','run_accounting.py')


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def generate(placement,timing,rss,source,binding,config):
    derived=placement.generate(timing,rss,source,binding,config)
    marker='Path(__file__).with_name("placement_adapter.py")'
    if derived.count(marker)!=1:raise ValueError('Unexpected placement adapter hook')
    derived=derived.replace(marker,'Path(__file__).with_name("accounting_adapter.py")')
    method='pidfd exit notification then exact-PID wait4; no periodic polling fallback'
    if derived.count(method)!=1:raise ValueError('Unexpected timing method description')
    return derived.replace(method,method+'; native pre-fork/post-wait4 accounting and four exclusive phases, unchanged full timer')


def build(root,config):
    package=Path(__file__).parent
    placement=load(package/'placement_build.py','_preserved_placement_builder');placement.build(root,config)
    for name in NEW_FILES:shutil.copyfile(package/name,root/name)
    source=load(root/'accounting_source.py','_exact_native_source')
    generated=root/'accounting_supervisor.c'
    generated.write_text(source.generate((root/'rss_supervisor.c').read_text()))
    cc=shutil.which('cc');common=[cc,'-O2','-std=c11','-Wall','-Wextra','-Werror']
    commands=[common+['-Dmain=rss_original_main','-c',str(generated),'-o',str(root/'accounting-original.o')],
        common+['-static','-DTARGET_CPU='+str(config['target_cpu']),'-o',str(root/'accounting-supervisor'),str(root/'placement_shim.c'),str(root/'accounting-original.o')],
        common+['-static','-o',str(root/'predecessor-control'),str(root/'predecessor_control.c')],
        ['readelf','-l',str(root/'accounting-supervisor')]]
    records=[]
    for command in commands:
        try:
            result=subprocess.run(command,capture_output=True,text=True,timeout=45)
            record={'argv':command,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
        except subprocess.TimeoutExpired as error:
            def decoded(value):return value.decode(errors='replace') if isinstance(value,bytes) else (value or '')
            records.append({'argv':command,'timeout':True,'stdout':decoded(error.stdout),'stderr':decoded(error.stderr)})
            (root/'accounting-build.json').write_text(json.dumps(records,indent=2)+'\n')
            raise RuntimeError('Accounting build timed out') from error
        records.append(record);(root/'accounting-build.json').write_text(json.dumps(records,indent=2)+'\n')
        if result.returncode:raise RuntimeError('Accounting build failed')
    if 'INTERP' in records[-1]['stdout']:raise RuntimeError('Accounting supervisor must be static')
    previous=json.loads((root/'placement-provenance.json').read_text())
    names=tuple(previous['binding'])+('optimize-linux-split.py','placement-provenance.json','accounting_supervisor.c',
        'accounting-original.o','accounting-supervisor','predecessor-control','accounting-build.json')+NEW_FILES
    rss=load(root/'rss_build.py','_original_rss_generator');timing=load(root/'timing_build.py','_original_timing_generator')
    binding={name:rss.sha(root/name) for name in names}
    derivative=root/'optimize-linux-accounting.py'
    derivative.write_text(generate(placement,timing,rss,(root/'optimize.py').read_text(),binding,config))
    receipt={'production_filename':derivative.name,'derivative_sha256':rss.sha(derivative),'binding':binding,
        'original_native_sha256':source.ORIGINAL_SHA256,'generated_native_sha256':rss.sha(generated),'cpu_choices':config,
        'method':'unchanged split/pidfd/full elapsed with parent pre-Popen and native pre-fork/post-wait4 accounting'}
    (root/'accounting-provenance.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return derivative
