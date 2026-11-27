"""Same RSS/timer with validated per-call static-shim placement evidence."""
import contextlib
import importlib.util
import os
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

_spec=importlib.util.spec_from_file_location('_original_placement_adapter',Path(__file__).with_name('placement_adapter.py'))
_original=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_original)
decode=_original.decode
decode_placement=_original.decode_placement
executable=_original.executable
COUNTERS=('minor_faults','major_faults','block_inputs','block_outputs','voluntary_switches','involuntary_switches')
ACCOUNTING_KEYS={'accounting_version','supervisor_pid','target_pid','wait_status','target_rss_kib','fork_monotonic_ns','reap_monotonic_ns','user_sec','user_usec','system_sec','system_usec'}|set(COUNTERS)


def decode_accounting(payload,pid,target_cpu):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate accounting key')
            result[key]=value
        return result
    if not payload or len(payload)>1696 or not payload.endswith(b'\n'):raise ValueError('Missing/oversized/truncated accounting report')
    lines=payload.splitlines(keepends=True)
    if len(lines)!=3 or any(len(line)>limit for line,limit in zip(lines,(160,512,1024))):raise ValueError('Expected three bounded private report lines')
    rss,placement=decode_placement(b''.join(lines[:2]),pid,target_cpu)
    account=json.loads(lines[2],object_pairs_hook=unique)
    if not isinstance(account,dict) or set(account)!=ACCOUNTING_KEYS or any(type(value) is not int or not 0<=value<(1<<63) for value in account.values()):
        raise ValueError('Invalid accounting fields/types/ranges')
    if (account['accounting_version']!=1 or account['supervisor_pid']!=pid or account['target_pid']!=rss['target_pid']
        or account['wait_status']!=rss['wait_status'] or account['target_rss_kib']!=rss['target_rss_kib']
        or account['user_usec']>=1000000 or account['system_usec']>=1000000
        or account['fork_monotonic_ns']>account['reap_monotonic_ns']):raise ValueError('Accounting identity/status/units/clock mismatch')
    return rss,placement,account


def phases(start,launch,popen,finish,account):
    fork=account['fork_monotonic_ns']/1e9;reap=account['reap_monotonic_ns']/1e9
    if (not all(type(v) in (float,int) and math.isfinite(v) for v in (start,launch,popen,finish))
        or not start<=launch<=fork<=reap<=finish or not launch<=popen<=finish):raise ValueError('Invalid parent/native phase ordering')
    result={'stream_preparation':launch-start,'launch_to_fork':fork-launch,'fork_to_reap':reap-fork,'report_to_end':finish-reap}
    tolerance=max(1e-9,8*math.ulp(float(start)),8*math.ulp(float(finish)))
    if not math.isclose(math.fsum(result.values()),finish-start,rel_tol=0,abs_tol=tolerance):raise ValueError('Four phases do not reconstruct full duration')
    return result


def measure(module, argv, cwd, environment, deadline, *, capture_version=False, record_effects=True):
    module['require'](hasattr(os, 'wait4'), 'this POSIX host lacks wait4')
    module['require'](time.monotonic() < deadline, 'invocation time budget exhausted before command')
    with tempfile.TemporaryDirectory(prefix='.stdio-', dir=cwd.parent) as scratch:
        stdout, stderr = Path(scratch) / 'stdout', Path(scratch) / 'stderr'
        receipt_path = Path(scratch) / 'rss-receipt'
        with receipt_path.open('w+b') as receipt:
            started = time.monotonic()
            with stdout.open('wb') as out, stderr.open('wb') as err:
                command = [str(Path(module['__file__']).parent / 'accounting-supervisor'), str(receipt.fileno()),
                           executable(argv[0], cwd, environment)] + list(argv)
                launch = time.monotonic()
                child = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                         stdout=out, stderr=err, start_new_session=True, pass_fds=(receipt.fileno(),))
                try:
                    popen_return = time.monotonic()
                    child.returncode, outer_rss, timeout = module['wait_for_child'](child, deadline)
                except BaseException:
                    module['kill_group'](child.pid)
                    with contextlib.suppress(ChildProcessError):
                        _, status, _ = os.wait4(child.pid, 0)
                        child.returncode = os.waitstatus_to_exitcode(status)
                    raise
            finished = time.monotonic()
            duration = finished - started
            residual = module['process_group_exists'](child.pid)
            if residual:
                module['kill_group'](child.pid)
            receipt.seek(0)
            payload = receipt.read(1697)
        record, placement, account, segments, error = None, None, None, None, None
        try:
            if timeout or residual or child.returncode != 0:
                raise ValueError('Supervisor incomplete: timeout/residual/nonzero status')
            record, placement, account = decode_accounting(payload, child.pid, module['_PLACEMENT']['target_cpu'])
            segments = phases(started,launch,popen_return,finished,account)
        except (ValueError, UnicodeError, TypeError) as failure:
            error = str(failure)
        observed = {
            'argv': argv, 'cwd': str(cwd), 'elapsed_seconds': duration,
            'timing_mode': module.get('_TIMING_MODE', 'pidfd'),
            'placement_mode': module.get('_PLACEMENT_MODE', 'split'), 'placement_receipt': placement,
            'started_monotonic': started, 'pre_popen_monotonic': launch,
            'accounting_receipt': account, 'exclusive_phases_seconds': segments, 'popen_return_monotonic': popen_return, 'finished_monotonic': finished,
            'peak_rss_bytes': record['target_rss_bytes'] if record and not record['floor_limited'] else None,
            'wait4_ru_maxrss': record['target_rss_kib'] if record else None, 'wait4_ru_maxrss_unit': 'KiB',
            'exit_code': record['exit_code'] if record else None, 'timeout': timeout,
            'residual_process_group': residual, 'rss_receipt': record,
            'private_report_raw': payload.decode('utf-8', 'replace'),
            'rss_receipt_raw': payload.splitlines(keepends=True)[1].decode('utf-8','replace') if len(payload.splitlines())==3 else None,
            'placement_receipt_raw': payload.splitlines(keepends=True)[0].decode('utf-8','replace') if len(payload.splitlines())==3 else None,
            'accounting_receipt_raw': payload.splitlines(keepends=True)[2].decode('utf-8','replace') if len(payload.splitlines())==3 else None,
            'instrument_error': error,
            'supervisor_pid': child.pid, 'outer_supervisor_exit_code': child.returncode, 'outer_supervisor_rss_kib': outer_rss,
            'stdout': module['stream_signature'](stdout), 'stderr': module['stream_signature'](stderr),
            'effects': module['tree'](cwd) if record_effects and not (timeout or residual or error) else None,
        }
        if capture_version:
            with stdout.open('rb') as stream:
                observed['version_output'] = stream.read(2048).decode('utf-8', 'replace').strip()
        if error and not (timeout or residual):
            evidence = Path(module['__file__']).parent / ('rss-invalid-' + uuid.uuid4().hex + '.json')
            module['save'](evidence, observed)
            raise module['Uncertain']('Invalid target RSS receipt; inspect ' + str(evidence))
        return observed
