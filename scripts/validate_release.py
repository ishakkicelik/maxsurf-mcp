"""Check release source contents, manifest and schema without touching COM."""
from pathlib import Path
import ast
import json
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from build_release import source_files
from release_licensing import validate_licenses


def main():
    files=list(source_files())
    for path in files:
        if path.suffix == '.py': ast.parse(path.read_text(encoding='utf-8-sig'))
        if path.suffix.lower() in ('.py','.md','.json','.toml','.txt','.yml','.ps1'):
            text=path.read_text(encoding='utf-8-sig')
            # Scan concrete credential shapes, not the word "password" in tests.
            if re.search(r'(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|sk-proj-[A-Za-z0-9_-]{30,}',text):
                raise SystemExit(f'Potential credential in {path.relative_to(ROOT)}')
    manifest=json.loads((ROOT/'extension/manifest.json').read_text())
    project=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']
    assert manifest['version'].replace('-rc.','rc') == project['version']
    assert manifest['compatibility']['platforms']==['win32']
    assert manifest['server']['type']=='python'
    assert manifest['server']['mcp_config']['args'][:2]==['-I','-S']
    assert (ROOT/'extension'/manifest['server']['entry_point']).is_file()
    licensing = validate_licenses(ROOT, files)
    import server
    try:
        actual={t.name:t.parameters for t in server.mcp._tool_manager.list_tools()}
        expected=json.loads((ROOT/'tests/data/tool_schemas.json').read_text())
        assert actual==expected, 'Tool schema snapshot differs'
        assert not ({'plan_motor_yacht','plan_usv_catamaran','hull_summary','evaluate_design','com_invoke'} & set(actual))
        assert not (ROOT/'maxsurf_mcp/design.py').exists()
        assert not (ROOT/'maxsurf_mcp/quality.py').exists()
        print(json.dumps({'status':'pass','source_files':len(files),'default_tools':len(actual),
                          'version':manifest['version'],'license':licensing['project_license']}))
    finally: server.shutdown()


if __name__=='__main__': main()
