"""Deterministic CPU choices and explicit singleton readback, outside timing."""
import json
import os
import subprocess
import sys


def choose(allowed):
    values=sorted(allowed)
    if len(values)<2 or any(type(value) is not int or not 0<=value<1024 for value in values) or len(set(values))!=len(values):
        raise RuntimeError('Need two distinct representable originally allowed CPU IDs')
    return {'host_cpu':values[-2],'target_cpu':values[-1],'initial_allowed_cpus':values,'runner':'ubuntu-24.04-arm'}


def set_parent(cpu):
    os.sched_setaffinity(0,{cpu})
    actual=sorted(os.sched_getaffinity(0))
    if actual!=[cpu]:raise RuntimeError('Parent CPU readback mismatch')
    return actual


def require_parent(cpu):
    actual=sorted(os.sched_getaffinity(0))
    if actual!=[cpu]:raise RuntimeError('Evaluator did not retain CPU H')
    return actual


def pin(root,save):
    config=choose(os.sched_getaffinity(0))
    save(root/'placement-config.json',config)
    proof={'config':config,'passed':False}
    save(root/'placement-affinity.json',proof)
    proof['wrapper_allowed_cpus']=set_parent(config['host_cpu'])
    result=subprocess.run([sys.executable,'-c','import json,os; print(json.dumps(sorted(os.sched_getaffinity(0))))'],capture_output=True,text=True,timeout=10)
    proof.update(child_returncode=result.returncode,child_stdout=result.stdout,child_stderr=result.stderr)
    if not result.returncode:proof['child_allowed_cpus']=json.loads(result.stdout)
    proof['passed']=not result.returncode and proof.get('child_allowed_cpus')==[config['host_cpu']]
    save(root/'placement-affinity.json',proof)
    if not proof['passed']:raise RuntimeError('Direct child did not inherit evaluator CPU H')
    return config
