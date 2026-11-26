"""Hash-bound Linux target-wait4 instrumentation; original evaluator stays intact."""
import contextlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import uuid

KEYS = {'version', 'supervisor_pid', 'target_pid', 'wait_status', 'target_rss_kib',
        'preexec_ceiling_bytes', 'exec_errno'}


def decode(payload, supervisor_pid):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate receipt field')
            result[key] = value
        return result
    if not payload or len(payload) > 512:
        raise ValueError('Missing or oversized supervisor receipt')
    record = json.loads(payload, object_pairs_hook=unique)
    if not isinstance(record, dict) or set(record) != KEYS or any(type(value) is not int for value in record.values()):
        raise ValueError('Invalid receipt fields/types')
    if (record['version'] != 1 or not 0 < record['supervisor_pid'] <= 2147483647
            or record['supervisor_pid'] != supervisor_pid
            or not 0 < record['target_pid'] <= 2147483647 or record['target_pid'] == supervisor_pid
            or not 0 <= record['target_rss_kib'] < (1 << 63) or record['preexec_ceiling_bytes'] <= 0
            or record['preexec_ceiling_bytes'] >= (1 << 63) or record['exec_errno'] < 0
            or record['exec_errno'] > 4095):
        raise ValueError('Invalid supervisor receipt values')
    page = os.sysconf('SC_PAGE_SIZE')
    if (type(page) is not int or page < 1024 or page & (page - 1)
            or record['preexec_ceiling_bytes'] < page
            or record['preexec_ceiling_bytes'] % page
            or (record['target_rss_kib'] * 1024) % page):
        raise ValueError('RSS receipt is not aligned to kernel pages')
    record['page_size_bytes'] = page
    status = record['wait_status']
    exited = 0 <= status <= 65535 and os.WIFEXITED(status) and status == os.WEXITSTATUS(status) << 8
    signaled = (0 <= status <= 255 and os.WIFSIGNALED(status)
                and 0 < os.WTERMSIG(status) < signal.NSIG and status & 127 != 127)
    if not (exited or signaled):
        raise ValueError('Invalid target wait status')
    if record['exec_errno']:
        raise ValueError('Target exec failed: errno ' + str(record['exec_errno']))
    record['exit_code'] = os.waitstatus_to_exitcode(status)
    record['target_rss_bytes'] = record['target_rss_kib'] * 1024
    record['floor_limited'] = record['target_rss_bytes'] <= record['preexec_ceiling_bytes']
    return record


def executable(argv0, cwd, environment):
    if '/' in argv0:
        return str((Path(cwd) / argv0).resolve())
    path = environment.get('PATH', os.defpath)
    entries = [str((Path(cwd) / entry).resolve()) for entry in path.split(os.pathsep)]
    return shutil.which(argv0, path=os.pathsep.join(entries)) or str(Path(entries[0]) / argv0)


def measure(module, argv, cwd, environment, deadline, *, capture_version=False, record_effects=True):
    module['require'](hasattr(os, 'wait4'), 'this POSIX host lacks wait4')
    module['require'](time.monotonic() < deadline, 'invocation time budget exhausted before command')
    with tempfile.TemporaryDirectory(prefix='.stdio-', dir=cwd.parent) as scratch:
        stdout, stderr = Path(scratch) / 'stdout', Path(scratch) / 'stderr'
        receipt_path = Path(scratch) / 'rss-receipt'
        with receipt_path.open('w+b') as receipt:
            started = time.monotonic()
            with stdout.open('wb') as out, stderr.open('wb') as err:
                command = [str(Path(module['__file__']).parent / 'rss-supervisor'), str(receipt.fileno()),
                           executable(argv[0], cwd, environment)] + list(argv)
                child = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                         stdout=out, stderr=err, start_new_session=True, pass_fds=(receipt.fileno(),))
                try:
                    child.returncode, outer_rss, timeout = module['wait_for_child'](child, deadline)
                except BaseException:
                    module['kill_group'](child.pid)
                    with contextlib.suppress(ChildProcessError):
                        _, status, _ = os.wait4(child.pid, 0)
                        child.returncode = os.waitstatus_to_exitcode(status)
                    raise
            duration = time.monotonic() - started
            residual = module['process_group_exists'](child.pid)
            if residual:
                module['kill_group'](child.pid)
            receipt.seek(0)
            payload = receipt.read(513)
        record, error = None, None
        try:
            if timeout or residual or child.returncode != 0:
                raise ValueError('Supervisor incomplete: timeout/residual/nonzero status')
            record = decode(payload, child.pid)
        except (ValueError, UnicodeError, TypeError) as failure:
            error = str(failure)
        observed = {
            'argv': argv, 'cwd': str(cwd), 'elapsed_seconds': duration,
            'peak_rss_bytes': record['target_rss_bytes'] if record and not record['floor_limited'] else None,
            'wait4_ru_maxrss': record['target_rss_kib'] if record else None, 'wait4_ru_maxrss_unit': 'KiB',
            'exit_code': record['exit_code'] if record else None, 'timeout': timeout,
            'residual_process_group': residual, 'rss_receipt': record,
            'rss_receipt_raw': payload.decode('utf-8', 'replace'), 'instrument_error': error,
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
