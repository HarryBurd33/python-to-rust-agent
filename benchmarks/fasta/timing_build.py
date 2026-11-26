"""Preserve the reviewed polling derivative and add separately bound timing methods."""
import ast
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess


EXISTING_SUPPORT_FILES = ('run.py','run_linux.py','bundle.json')
NEW_FILES = ('timing_wait.py', 'timing_adapter.py', 'timing_build.py', 'timing_control.c',
             'timing_controls.py', 'timing_tests.py', 'run_timing.py')


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def generate(builder, source, binding, mode):
    if mode not in ('polling', 'pidfd'):
        raise ValueError('Unknown timing method')
    derived = builder.generate(source, binding)
    expected = 'Path(__file__).with_name("rss_adapter.py")'
    if derived.count(expected) != 1:
        raise ValueError('Unexpected adapter import')
    derived = derived.replace(expected, 'Path(__file__).with_name("timing_adapter.py")')
    marker = '_rss_spec = _rss_import.spec_from_file_location'
    addition = ('_TIMING_MODE = ' + repr(mode) + '\n'
                '_wait_spec = _rss_import.spec_from_file_location("_bound_timing_wait", Path(__file__).with_name("timing_wait.py"))\n'
                '_timing_wait = _rss_import.module_from_spec(_wait_spec)\n'
                '_wait_spec.loader.exec_module(_timing_wait)\n' + marker)
    # Dependency execution follows the complete immutable binding check loop.
    derived = derived.replace(marker, addition, 1)
    if mode == 'pidfd':
        start = derived.index('def wait_for_child(')
        stop = derived.index('\ndef stream_signature(', start)
        derived = derived[:start] + ('def wait_for_child(child, deadline):\n'
                    '    return _timing_wait.wait_for_child(child, deadline, kill_group)\n\n') + derived[stop:]
    method = ('monotonic original full launch-to-supervisor-reap; raw start/Popen-return/end endpoints; '
              + ('pidfd exit notification then exact-PID wait4; no periodic polling fallback' if mode == 'pidfd'
                 else 'previous nonblocking wait4 with up to 0.5 ms polling sleep'))
    if derived.count(builder.TIMING) != 1:
        raise ValueError('Unexpected timing metadata')
    derived = derived.replace(builder.TIMING, method)
    def functions(text):
        return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)}
    old, new = functions(source), functions(derived)
    permitted = {'measure', 'host_info', 'report_evaluation'} | ({'wait_for_child'} if mode == 'pidfd' else set())
    if any(old[name] != new[name] for name in set(old)-permitted):
        raise ValueError('Unapproved timing derivative function change')
    return derived


def build(root):
    package = Path(__file__).parent
    builder = load(package/'rss_build.py', '_original_rss_builder')
    previous = builder.build(root)  # Keep optimize-linux-rss.py and rss-provenance.json.
    for name in EXISTING_SUPPORT_FILES + NEW_FILES:
        shutil.copyfile(package/name, root/name)
    compiler = shutil.which('cc')
    command = [compiler, '-static', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror',
               '-o', str(root/'timing-control'), str(root/'timing_control.c')]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=45)
        record = {'argv':command,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
    except subprocess.TimeoutExpired as error:
        def decoded(value): return value.decode(errors='replace') if isinstance(value,bytes) else (value or '')
        record = {'argv':command,'timeout':True,'stdout':decoded(error.stdout),'stderr':decoded(error.stderr)}
        (root/'timing-build.json').write_text(json.dumps(record,indent=2)+'\n')
        raise RuntimeError('Timing control build timed out') from error
    (root/'timing-build.json').write_text(json.dumps(record,indent=2)+'\n')
    if result.returncode:
        raise RuntimeError('Timing control build failed')
    names = ('optimize.py','rss_supervisor.c','rss-supervisor','rss_adapter.py','rss_build.py','rss-build.json',
             'rss_diagnostics.py','test_rss.py','rss_fixture.c','rss-fixture','optimize-linux-rss.py','rss-provenance.json',
             'timing-control','timing-build.json') + EXISTING_SUPPORT_FILES + NEW_FILES
    binding = {name:builder.sha(root/name) for name in names}
    paths = {}
    for mode, filename in (('polling','optimize-linux-timing-polling.py'),('pidfd','optimize-linux-pidfd.py')):
        path = root/filename
        path.write_text(generate(builder,(root/'optimize.py').read_text(),binding,mode))
        paths[mode] = {'filename':filename,'sha256':builder.sha(path)}
    provenance = {'production_mode':'pidfd','production_filename':paths['pidfd']['filename'],
                  'previous_polling_filename':previous.name,'previous_polling_sha256':builder.sha(previous),
                  'derivatives':paths,'binding':binding}
    (root/'timing-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    return root/paths['pidfd']['filename']
