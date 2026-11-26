"""Build a static instrument and explicitly identified derivative, never edit frozen bytes."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

FROZEN = 'c9c049ee96b2d0613c4cdc44db100e7e3c1d44a9be160f4831f97764d0410cc2'
MEMORY = 'static Linux supervisor wait4 exact fork/exec target KiB; comparable only above per-call pre-exec VmSize ceiling; process-tree peaks unavailable'
TIMING = 'monotonic launch-to-supervisor-reap including helper launch/fork/exec/target/reap; original outer wait4 polling at up to 0.5 ms'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(source, binding):
    if hashlib.sha256(source.encode()).hexdigest() != FROZEN:
        raise ValueError('Frozen evaluator identity changed')
    start = source.index('def measure(')
    end = source.index('\ndef bounded_measure(', start)
    original = source[start:end]
    expected = ("def measure(argv, cwd, environment, deadline, *, capture_version=False, record_effects=True):\n"
                "    require(hasattr(os, \"wait4\"), \"this POSIX host lacks wait4\")\n")
    if not original.startswith(expected) or original.count('subprocess.Popen(') != 1:
        raise ValueError('Unexpected frozen measurement block')
    setup = '''import importlib.util as _rss_import
_RSS_BINDING = BINDING
for _rss_name, _rss_sha in _RSS_BINDING.items():
    require(file_digest(Path(__file__).parent / _rss_name) == _rss_sha, "RSS instrumentation binding changed: " + _rss_name)
_rss_spec = _rss_import.spec_from_file_location("_bound_rss_adapter", Path(__file__).with_name("rss_adapter.py"))
_rss_adapter = _rss_import.module_from_spec(_rss_spec)
_rss_spec.loader.exec_module(_rss_adapter)


def measure(argv, cwd, environment, deadline, *, capture_version=False, record_effects=True):
    return _rss_adapter.measure(globals(), argv, cwd, environment, deadline,
                                capture_version=capture_version, record_effects=record_effects)

'''.replace('= BINDING\n', '= ' + repr(binding) + '\n', 1)
    derived = source[:start] + setup + source[end:]
    replacements = {
        'wait4 ru_maxrss for the directly launched PID; Darwin bytes, Linux KiB': MEMORY,
        'wait4 directly launched PID peak RSS; process-tree peaks are unavailable': MEMORY,
        'monotonic launch-to-reap wall time; wait4 sampled at up to 0.5 ms sleep intervals': TIMING,
    }
    for old, new in replacements.items():
        if derived.count(old) != 1:
            raise ValueError('Unexpected method description count')
        derived = derived.replace(old, new)
    oldtree, newtree = ast.parse(source), ast.parse(derived)
    allowed = {'measure', 'host_info', 'report_evaluation'}
    oldfunctions = {n.name: ast.dump(n, include_attributes=False) for n in oldtree.body if isinstance(n, ast.FunctionDef) and n.name not in allowed}
    newfunctions = {n.name: ast.dump(n, include_attributes=False) for n in newtree.body if isinstance(n, ast.FunctionDef) and n.name not in allowed}
    if oldfunctions != newfunctions:
        raise ValueError('Unapproved evaluator function change')
    return derived


def build(root):
    package = Path(__file__).parent
    for name in ('rss_supervisor.c', 'rss_adapter.py', 'rss_build.py', 'rss_diagnostics.py', 'rss_fixture.c', 'test_rss.py'):
        shutil.copyfile(package / name, root / name)
    compiler = shutil.which('cc')
    if not compiler:
        raise RuntimeError('Native C compiler unavailable')
    command = [compiler, '-static', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror',
               '-o', str(root / 'rss-supervisor'), str(root / 'rss_supervisor.c')]
    records = []
    for argv in ([compiler, '--version'], command,
                 [compiler, '-static', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror',
                  '-o', str(root / 'rss-fixture'), str(root / 'rss_fixture.c')],
                 ['readelf', '-l', str(root / 'rss-supervisor')]):
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=45)
        except subprocess.TimeoutExpired as error:
            def decoded(value):
                return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
            records.append({'argv': argv, 'timeout': True, 'stdout': decoded(error.stdout), 'stderr': decoded(error.stderr)})
            (root / 'rss-build.json').write_text(json.dumps(records, indent=2) + '\n')
            raise RuntimeError('RSS helper build/inspection timed out') from error
        records.append({'argv': argv, 'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        (root / 'rss-build.json').write_text(json.dumps(records, indent=2) + '\n')
        if result.returncode:
            raise RuntimeError('RSS helper build/inspection failed')
    if 'INTERP' in records[-1]['stdout']:
        raise RuntimeError('RSS helper must be statically linked')
    names = ('optimize.py', 'rss_supervisor.c', 'rss-supervisor', 'rss_adapter.py', 'rss_build.py', 'rss-build.json')
    binding = {name: sha(root / name) for name in names}
    derivative = root / 'optimize-linux-rss.py'
    derivative.write_text(generate((root / 'optimize.py').read_text(), binding))
    provenance = {'original_evaluator_sha256': FROZEN, 'derivative_sha256': sha(derivative),
                  'binding': binding, 'fixture_source_sha256': sha(root / 'rss_fixture.c'),
                  'fixture_binary_sha256': sha(root / 'rss-fixture'),
                  'diagnostics_sha256': sha(root / 'rss_diagnostics.py'), 'tests_sha256': sha(root / 'test_rss.py')}
    (root / 'rss-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    return derivative
