#!/usr/bin/env python3
"""One pidfd comparison after fixed production-path qualification and controls."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def load(path,name):
    spec = importlib.util.spec_from_file_location(name,path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


legacy = load(Path(__file__).with_name('run_linux.py'),'_unchanged_linux_wrapper')
save = legacy.save


def qualify(root):
    builder = load(Path(__file__).with_name('timing_build.py'),'_timing_builder')
    derivative = builder.build(root)
    try:
        result = subprocess.run([sys.executable,str(root/'timing_tests.py'),'--root',str(root)],
                                capture_output=True,text=True,timeout=90)
    except subprocess.TimeoutExpired as error:
        def decoded(value): return value.decode(errors='replace') if isinstance(value,bytes) else (value or '')
        (root/'timing-tests.log').write_text(decoded(error.stdout)+decoded(error.stderr))
        raise RuntimeError('Production timing tests timed out; no FASTA comparison started') from error
    (root/'timing-tests.log').write_text(result.stdout+result.stderr)
    if result.returncode:
        raise RuntimeError('Production timing/RSS tests failed; no FASTA comparison started')
    diagnostics = load(root/'rss_diagnostics.py','_unchanged_rss_qualification')
    original_load = diagnostics.load
    def production_load(path,name):
        return original_load(derivative if path.name=='optimize-linux-rss.py' else path,name)
    diagnostics.load = production_load
    save(root/'production-qualification-routing.json',{
        'prior_tests':'test_rss.py: unchanged ProtocolTests plus inherited NativeTests methods',
        'test_adapter':'timing_adapter.py','native_test_derivative':derivative.name,
        'rss_qualification_source':'rss_diagnostics.py unchanged; explicit load hook',
        'rss_qualification_repaired_derivative':derivative.name,
        'historical_direct_control':'optimize.py unchanged',
        'production_mode':'pidfd'})
    diagnostics.qualify(root)
    controls = load(root/'timing_controls.py','_fixed_timing_controls')
    controls.run(root)
    return derivative


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--destination',required=True,type=Path)
    parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    root=args.destination.resolve()
    package=legacy.frozen.restore(root)
    save(root/'snapshot-manifest.json',{k:{'sha256':v['sha256'],'size':v['size']} for k,v in package['files'].items()})
    if args.verify_only:
        print('Verified exact bundle:',len(package['files']),'files; no benchmark ran')
        return
    if platform.system()!='Linux' or platform.machine()!='aarch64':
        raise RuntimeError('Requires the predeclared Linux aarch64 runner')
    if sys.version_info[:3]!=(3,15,0):
        raise RuntimeError('Requires Python 3.15.0')
    selected=legacy.pin_cpu(root)
    legacy.frozen.compiler_preflight(root)
    legacy.frozen.capture(root,'python-version.txt',[sys.executable,'--version'])
    save(root/'runner.json',{k:os.environ.get(k) for k in ('RUNNER_OS','RUNNER_ARCH','ImageOS','ImageVersion','GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')})
    legacy.host_info(root)
    derivative=qualify(root)
    legacy.check_readiness(root,selected)
    affinity=sorted(os.sched_getaffinity(0))
    save(root/'affinity-postreadiness.json',{'selected_cpu':selected,'allowed_cpus':affinity,'passed':affinity==[selected]})
    if affinity!=[selected]:
        raise RuntimeError('CPU affinity changed before measurement')
    try:
        common=[sys.executable,str(derivative)]
        arguments=[str(root/'contract.json'),'--runs',str(root/'runs'),'--expect-contract',legacy.CONTRACT]
        for action in ('baseline','evaluate'):
            command=common+[action]+arguments
            if action=='evaluate':
                command+=['--candidate',str(root/'candidate')]
            with (root/(action+'-result.json')).open('w') as out,(root/(action+'-stderr.txt')).open('w') as err:
                completed=subprocess.run(command,stdout=out,stderr=err,timeout=240)
            if completed.returncode:
                raise RuntimeError(action+' failed; preserve evidence, do not retry')
    finally:
        legacy.snapshot(root,'host-after.json')
        save(root/'affinity-after.json',{'allowed_cpus':sorted(os.sched_getaffinity(0))})
    result=json.loads((root/'evaluate-result.json').read_text())
    report=json.loads(Path(result['report_json']).read_text())
    print(json.dumps({'status':report['status'],'parity':report['parity'],
        'workloads':[{'id':w['id'],'metrics':w['metrics']} for w in report['workloads']]},indent=2))
    print('Pidfd full launch-to-reap comparison; independent review required, no automatic attestation/adoption.')


if __name__=='__main__': main()
