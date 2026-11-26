"""Timestamped copy of reviewed RSS measurement; target attribution is unchanged."""
import contextlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

_spec = importlib.util.spec_from_file_location('_timing_original_rss', Path(__file__).with_name('rss_adapter.py'))
_rss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rss)
decode = _rss.decode
executable = _rss.executable


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
            'timing_mode': module.get('_TIMING_MODE', 'pidfd'),
            'started_monotonic': started, 'popen_return_monotonic': popen_return, 'finished_monotonic': finished,
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
