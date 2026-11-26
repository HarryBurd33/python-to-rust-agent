"""Unit tests everywhere; --root adds bounded Linux native checks before FASTA."""
import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('rss_adapter_test', HERE / 'rss_adapter.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
ROOT = None


def receipt(**changes):
    data = dict(version=1, supervisor_pid=100, target_pid=101, wait_status=0,
                target_rss_kib=4096, preexec_ceiling_bytes=1024*1024, exec_errno=0)
    data.update(changes)
    return json.dumps(data).encode()


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        page = patch.object(adapter.os, 'sysconf', return_value=4096)
        page.start(); self.addCleanup(page.stop)

    def test_success_nonzero_and_signal_statuses(self):
        for raw, expected in ((0, 0), (2 << 8, 2), (127 << 8, 127), (signal.SIGTERM, -signal.SIGTERM)):
            value = adapter.decode(receipt(wait_status=raw), 100)
            self.assertEqual(value['exit_code'], expected)
            self.assertFalse(value['floor_limited'])
            self.assertEqual(value['target_rss_bytes'], 4096*1024)

    def test_floor_is_unavailable_without_subtraction(self):
        for rss in (0, 1024):
            self.assertTrue(adapter.decode(receipt(target_rss_kib=rss), 100)['floor_limited'])
        self.assertFalse(adapter.decode(receipt(target_rss_kib=1028), 100)['floor_limited'])

    def test_malformed_forged_incomplete_and_exec_failure(self):
        values = [b'', receipt()[:20], receipt()+b'{}', b'x'*513,
                  receipt(supervisor_pid=99), receipt(supervisor_pid=1<<32), receipt(target_pid=1<<32), receipt(target_pid=100), receipt(target_pid=-1),
                  receipt(preexec_ceiling_bytes=1), receipt(preexec_ceiling_bytes=4097), receipt(target_rss_kib=1025),
                  receipt(version=2), receipt(target_rss_kib=-1), receipt(target_rss_kib=1<<63), receipt(preexec_ceiling_bytes=1<<63), receipt(preexec_ceiling_bytes=0),
                  receipt(wait_status=0x7f), receipt(wait_status=65536), receipt(exec_errno=2),
                  receipt(target_rss_kib=True), receipt().replace(b'"version": 1', b'"version":1,"version":1')]
        for value in values:
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                adapter.decode(value, 100)

    def test_path_resolution_uses_target_cwd_and_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            cwd = Path(temp)
            (cwd / 'bin').mkdir()
            target = cwd / 'bin/fixture'
            target.write_text('fixture'); target.chmod(0o755)
            self.assertEqual(adapter.executable('fixture', cwd, {'PATH':'bin'}), str(target.resolve()))
            self.assertEqual(adapter.executable('./bin/fixture', cwd, {}), str(target.resolve()))

    def test_missing_path_target_never_executes_cwd_decoy(self):
        with tempfile.TemporaryDirectory() as temp:
            cwd = Path(temp)
            (cwd/'empty').mkdir()
            (cwd/'decoy').write_text('decoy'); (cwd/'decoy').chmod(0o755)
            self.assertEqual(adapter.executable('decoy', cwd, {'PATH':'empty'}), str((cwd/'empty/decoy').resolve()))
            self.assertEqual(adapter.executable('decoy', cwd, {'PATH':''}), str((cwd/'decoy').resolve()))

    def mocked_measure(self, payload, *, residual=False, timeout=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        work = root / 'work'; work.mkdir()
        events = []
        class Uncertain(RuntimeError): pass
        def save(path, value): path.write_text(json.dumps(value))
        module = {'__file__': str(root / 'derived.py'), 'require': lambda value, message: value or (_ for _ in ()).throw(ValueError(message)),
                  'wait_for_child': lambda child, deadline: (0, 99999, timeout),
                  'process_group_exists': lambda pid: residual,
                  'kill_group': lambda pid: events.append('kill'), 'stream_signature': lambda path: {'size':path.stat().st_size},
                  'tree': lambda path: [], 'save':save, 'Uncertain':Uncertain}
        def popen(argv, **kwargs):
            if payload: os.write(kwargs['pass_fds'][0], payload)
            self.assertEqual(argv[3:], ['/target', 'arg'])
            self.assertEqual(kwargs['stdin'], adapter.subprocess.DEVNULL)
            self.assertTrue(kwargs['start_new_session'])
            return type('Child', (), {'pid':100, 'returncode':None})()
        with patch.object(adapter.subprocess, 'Popen', side_effect=popen):
            if not payload and not (timeout or residual):
                with self.assertRaises(Uncertain):
                    adapter.measure(module, ['/target', 'arg'], work, {}, time.monotonic()+1)
                self.assertEqual(len(list(root.glob('rss-invalid-*.json'))), 1)
                result = json.loads(next(root.glob('rss-invalid-*.json')).read_text())
            else:
                result = adapter.measure(module, ['/target','arg'], work, {}, time.monotonic()+1)
        return result, events

    def test_adapter_binds_original_argv_and_returns_target_peak(self):
        result, events = self.mocked_measure(receipt())
        self.assertEqual(result['peak_rss_bytes'], 4096*1024)
        self.assertEqual(result['outer_supervisor_rss_kib'], 99999)
        self.assertEqual(result['exit_code'], 0)
        self.assertFalse(events)

    def test_adapter_floor_retains_raw_status_and_null_comparable_peak(self):
        result, events = self.mocked_measure(receipt(target_rss_kib=1024))
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['wait4_ru_maxrss'], 1024)
        self.assertIsNone(result['peak_rss_bytes'])
        self.assertTrue(result['rss_receipt']['floor_limited'])
        self.assertIsNone(result['instrument_error'])
        self.assertFalse(events)

    def test_invalid_receipt_retained_and_no_target_status_fabricated(self):
        result, events = self.mocked_measure(b'')
        self.assertIsNone(result['exit_code'])
        self.assertIsNone(result['peak_rss_bytes'])
        self.assertTrue(result['instrument_error'])

    def test_timeout_or_residual_kills_group_and_preserves_unavailable_evidence(self):
        for kwargs in ({'timeout':True}, {'residual':True}):
            result, events = self.mocked_measure(b'', **kwargs)
            self.assertIsNone(result['exit_code'])
            self.assertIsNone(result['peak_rss_bytes'])
            if kwargs.get('residual'): self.assertEqual(events, ['kill'])


class NativeTests(unittest.TestCase):
    def setUp(self):
        if ROOT is None:
            self.skipTest('Linux integration requires --root after static build')
        spec = importlib.util.spec_from_file_location('derived_native_test', ROOT / 'optimize-linux-rss.py')
        self.module = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name)
        self.environment = self.module.runtime_environment()

    def call(self, args, deadline=5):
        return self.module.measure([str(ROOT/'rss-fixture')] + args, self.cwd, self.environment,
                                   time.monotonic()+deadline, capture_version=True)

    def no_live_group(self, pid):
        # Killed orphan zombies are recorded but are not live descendants.
        for _ in range(100):
            live = []
            for entry in Path('/proc').iterdir():
                if not entry.name.isdigit(): continue
                try:
                    fields = (entry/'stat').read_text().rsplit(')',1)[1].split()
                    if int(fields[2]) == pid and fields[0] != 'Z': live.append(entry.name)
                except (OSError, ValueError, IndexError): pass
            if not live: return
            time.sleep(0.01)
        self.fail('Live processes remain in tested group: ' + repr(live))

    def test_exit_2_127_signal_and_private_fds(self):
        for code in (0,2,127): self.assertEqual(self.call(['exit',str(code)])['exit_code'], code)
        self.assertEqual(self.call(['signal'])['exit_code'], -signal.SIGTERM)
        self.assertEqual(self.call(['fds'])['version_output'], 'fds-closed')

    def test_real_exec_error_retains_receipt_and_refuses(self):
        before = set(ROOT.glob('rss-invalid-*.json'))
        with self.assertRaises(self.module.Uncertain):
            self.module.measure([str(self.cwd/'missing')], self.cwd, self.environment, time.monotonic()+5)
        created = set(ROOT.glob('rss-invalid-*.json')) - before
        self.assertEqual(len(created), 1)
        record = json.loads(created.pop().read_text())
        self.assertIn('exec failed', record['instrument_error'])
        self.assertIsNone(record['exit_code'])

    def test_exact_argv_env_cwd_stdio_and_path(self):
        self.environment['RSS_TEST'] = 'exact environment'
        self.environment['PATH'] = str(ROOT) + os.pathsep + self.environment.get('PATH','')
        result = self.module.measure(['rss-fixture','io','argument with spaces'], self.cwd,
                                     self.environment, time.monotonic()+5, capture_version=True)
        self.assertEqual(result['version_output'], 'argv0=rss-fixture\narg=argument with spaces\ncwd='+str(self.cwd)+'\nenv=exact environment\nstdin=0')
        import hashlib
        self.assertEqual(result['stderr'], {'size':15, 'sha256':hashlib.sha256(b'fixture-stderr\n').hexdigest()})

    def test_timeout_and_residual_cleanup(self):
        timed = self.call(['sleep'], deadline=0.05)
        self.assertTrue(timed['timeout'])
        self.assertIsNone(timed['peak_rss_bytes'])
        # Supervisor PID is retained even when the group kill prevents a receipt.
        self.no_live_group(timed['supervisor_pid'])
        residual = self.call(['residual'])
        self.assertTrue(residual['residual_process_group'])
        self.assertIsNone(residual['peak_rss_bytes'])
        self.no_live_group(residual['supervisor_pid'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    ROOT = args.root.resolve() if args.root else None
    unittest.main(argv=[sys.argv[0]], verbosity=2)
