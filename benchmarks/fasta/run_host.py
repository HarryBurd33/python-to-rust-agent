#!/usr/bin/env python3
"""Thin diagnostic routing around the unchanged qualified production instrument."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


HERE=Path(__file__).parent
legacy=load(HERE/'run_linux.py','_original_linux_wrapper')
save=legacy.save


def qualify(root,config):
    builder=load(HERE/'accounting_build.py','_accounting_builder')
    derivative=builder.build(root,config)
    try:
        result=subprocess.run([sys.executable,str(root/'accounting_tests.py'),'--root',str(root)],capture_output=True,text=True,timeout=90)
    except subprocess.TimeoutExpired as error:
        def decoded(value):return value.decode(errors='replace') if isinstance(value,bytes) else (value or '')
        (root/'accounting-tests.log').write_text(decoded(error.stdout)+decoded(error.stderr))
        raise RuntimeError('Accounting tests timed out; no FASTA comparison started') from error
    (root/'accounting-tests.log').write_text(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError('Accounting native tests failed; no FASTA comparison started')
    diagnostics=load(root/'rss_diagnostics.py','_original_rss_qualification')
    original=diagnostics.load
    diagnostics.load=lambda path,name:original(derivative if path.name=='optimize-linux-rss.py' else path,name)
    save(root/'accounting-qualification-routing.json',{'production_derivative':derivative.name,'placement':'split',
        'rss_qualification_source':'unchanged rss_diagnostics.py with explicit production load hook',
        'legacy_protocol_unit_source':'unchanged test_rss.ProtocolTests; labeled legacy-only',
        'native_tests':'unchanged RSS NativeTests methods and new placement/pidfd tests on actual new path'})
    diagnostics.qualify(root)
    diagnostic_builder=load(HERE/'host_build.py','_separate_diagnostic_builder')
    diagnostic_builder.build(root,derivative)
    try:
        checked=subprocess.run([sys.executable,str(root/'host_tests.py')],capture_output=True,text=True,timeout=45)
    except subprocess.TimeoutExpired as error:
        def decoded(value):return value.decode(errors='replace') if isinstance(value,bytes) else (value or '')
        (root/'host-tests.log').write_text(decoded(error.stdout)+decoded(error.stderr))
        raise RuntimeError('Diagnostic fixture tests timed out') from error
    (root/'host-tests.log').write_text(checked.stdout+checked.stderr)
    if checked.returncode:raise RuntimeError('Diagnostic fixture tests failed; no FASTA comparison started')
    controls=load(root/'host_controls.py','_fixed_python_first_controls')
    controls.run(root,config)
    return derivative


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--destination',required=True,type=Path);parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args();root=args.destination.resolve()
    package=legacy.frozen.restore(root)
    save(root/'snapshot-manifest.json',{name:{'sha256':row['sha256'],'size':row['size']} for name,row in package['files'].items()})
    if args.verify_only:
        print('Verified exact bundle:',len(package['files']),'files; no benchmark ran');return
    if platform.system()!='Linux' or platform.machine()!='aarch64':raise RuntimeError('Requires Linux aarch64')
    if sys.version_info[:3]!=(3,15,0):raise RuntimeError('Requires Python 3.15.0')
    affinity=load(HERE/'placement_config.py','_selected_cpu_choices')
    config=affinity.pin(root,save)
    legacy.frozen.compiler_preflight(root);legacy.frozen.capture(root,'python-version.txt',[sys.executable,'--version'])
    save(root/'runner.json',{key:os.environ.get(key) for key in ('RUNNER_OS','RUNNER_ARCH','ImageOS','ImageVersion','GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')})
    legacy.host_info(root)
    derivative=qualify(root,config)
    readiness=load(HERE/'placement_readiness.py','_dual_cpu_readiness');readiness.check_readiness(root,config)
    host=affinity.require_parent(config['host_cpu'])
    save(root/'placement-postreadiness.json',{'parent_cpus':host,'expected':config['host_cpu'],'passed':True})
    try:
        common=[sys.executable,str(derivative)]
        arguments=[str(root/'contract.json'),'--runs',str(root/'runs'),'--expect-contract',legacy.CONTRACT]
        for action in ('baseline','evaluate'):
            before=affinity.require_parent(config['host_cpu'])
            save(root/('placement-'+action+'-before.json'),{'parent_cpus':before,'passed':True})
            command=common+[action]+arguments
            if action=='evaluate':command+=['--candidate',str(root/'candidate')]
            with (root/(action+'-result.json')).open('w') as out,(root/(action+'-stderr.txt')).open('w') as err:
                completed=subprocess.run(command,stdout=out,stderr=err,timeout=240)
            after=affinity.require_parent(config['host_cpu'])
            save(root/('placement-'+action+'-after.json'),{'parent_cpus':after,'passed':True})
            if completed.returncode:raise RuntimeError(action+' failed; preserve evidence, do not retry')
    finally:
        legacy.snapshot(root,'host-after.json')
        actual=sorted(os.sched_getaffinity(0))
        save(root/'placement-after.json',{'parent_cpus':actual,'expected':config['host_cpu'],'passed':actual==[config['host_cpu']]})
        if actual!=[config['host_cpu']]:raise RuntimeError('Wrapper lost evaluator CPU H')
    result=json.loads((root/'evaluate-result.json').read_text());report=json.loads(Path(result['report_json']).read_text())
    print(json.dumps({'status':report['status'],'parity':report['parity'],'workloads':[{'id':row['id'],'metrics':row['metrics']} for row in report['workloads']]},indent=2))
    print('Native-accounting split comparison; independent review required, no automatic adoption.')


if __name__=='__main__':main()
