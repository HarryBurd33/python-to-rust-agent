"""Separate diagnostic manifest; never changes production construction/bindings."""
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

FILES=('python_startup.py','crossover_predecessor.c','python_first_build.py','python_first_controls.py','python_first_tests.py','run_python_first.py')
MODULES=('argparse','csv','hashlib','importlib.metadata','importlib.util','io','json','math','os','pathlib','platform','shutil','subprocess','sys','datetime')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_imports(original,script):
    expected=[node for node in ast.parse(original).body if isinstance(node,(ast.Import,ast.ImportFrom))][:15]
    actual=[node for node in ast.parse(script).body if isinstance(node,(ast.Import,ast.ImportFrom))]
    if len(expected)!=15 or [ast.dump(n,include_attributes=False) for n in expected]!=[ast.dump(n,include_attributes=False) for n in actual]:
        raise ValueError('Python startup imports differ from frozen research_tools.py')


def elf_identity(header,programs,dynamic,static=False):
    if not re.search(r'Class:\s+ELF64',header) or not re.search(r'Machine:\s+AArch64',header):raise ValueError('Expected ELF64 AArch64')
    matches=re.findall(r'Requesting program interpreter:\s*([^\]]+)\]',programs)
    if static:
        if matches or re.search(r'^\s*INTERP\s',programs,re.M) or '(NEEDED)' in dynamic:raise ValueError('Native predecessor must be static')
        return {'architecture':'AArch64','static':True}
    if len(matches)!=1 or not Path(matches[0]).is_absolute() or Path(matches[0]).name!='ld-linux-aarch64.so.1':raise ValueError('Invalid AArch64 Linux PT_INTERP')
    if not re.search(r'\(NEEDED\).*\[libc\.so\.6\]',dynamic):raise ValueError('Dynamic probe must depend on libc.so.6')
    return {'architecture':'AArch64','static':False,'interpreter':matches[0],'needed_libc':'libc.so.6'}


def build(root,production):
    package=Path(__file__).parent
    bindings=json.loads((root/'accounting-provenance.json').read_text())['binding']
    if len(bindings)!=55:raise RuntimeError('Expected unchanged 55 production input bindings')
    before={name:sha(root/name) for name in bindings}
    if before!=bindings:raise RuntimeError('Production input binding changed before diagnostic setup')
    production_sha=sha(production)
    for name in FILES:shutil.copyfile(package/name,root/name)
    verify_imports((root/'source/research_tools.py').read_text(),(root/'python_startup.py').read_text())
    cc=shutil.which('cc');common=[cc,'-O2','-std=c11','-Wall','-Wextra','-Werror']
    records=[]
    def command(argv):
        try:
            result=subprocess.run(argv,capture_output=True,text=True,timeout=45)
            record={'argv':argv,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
        except subprocess.TimeoutExpired as error:
            def text(value):return value.decode(errors='replace') if isinstance(value,bytes) else (value or '')
            records.append({'argv':argv,'timeout':True,'stdout':text(error.stdout),'stderr':text(error.stderr)})
            (root/'python-first-build.json').write_text(json.dumps(records,indent=2)+'\n');raise RuntimeError('Diagnostic build timed out') from error
        records.append(record);(root/'python-first-build.json').write_text(json.dumps(records,indent=2)+'\n')
        if result.returncode:raise RuntimeError('Diagnostic build/ELF inspection failed')
        return result.stdout
    command([cc,'--version'])
    command(common+['-static','-o',str(root/'crossover-predecessor'),str(root/'crossover_predecessor.c')])
    command(common+['-o',str(root/'dynamic-probe'),str(root/'placement_control.c')])
    native=elf_identity(command(['readelf','-h',str(root/'crossover-predecessor')]),command(['readelf','-l',str(root/'crossover-predecessor')]),command(['readelf','-d',str(root/'crossover-predecessor')]),True)
    probe=elf_identity(command(['readelf','-h',str(root/'dynamic-probe')]),command(['readelf','-l',str(root/'dynamic-probe')]),command(['readelf','-d',str(root/'dynamic-probe')]))
    interpreter=Path(probe['interpreter']);interpreter_header=command(['readelf','-h',str(interpreter)])
    if not re.search(r'Machine:\s+AArch64',interpreter_header):raise RuntimeError('Interpreter ELF architecture mismatch')
    probe.update(interpreter_realpath=str(interpreter.resolve()),interpreter_sha256=sha(interpreter))
    if sha(production)!=production_sha or any(sha(root/name)!=value for name,value in before.items()):raise RuntimeError('Production bytes changed during diagnostic setup')
    names=FILES+('crossover-predecessor','dynamic-probe','placement_control.c','python-first-build.json')
    manifest={'production_evaluator':production.name,'production_evaluator_sha256':production_sha,'production_bindings':before,
        'diagnostic_files':{name:sha(root/name) for name in names},'native_elf':native,'probe_elf':probe,'expected_modules':list(MODULES),'import_source_sha256':sha(root/'source/research_tools.py')}
    (root/'python-first-setup.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
