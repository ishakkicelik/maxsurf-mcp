"""The acceptance oracle must reject bad servers even under python -O."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
STUB = '''
import json, sys
schemas = json.loads({schemas!r})
mode = {mode!r}
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    if method == "initialize":
        result = {{"protocolVersion": request["params"]["protocolVersion"],
                   "capabilities": {{"tools": {{}}}},
                   "serverInfo": {{"name": "stub", "version":
                       "wrong" if mode == "version" else "0.2.0-rc.5"}}}}
    elif method == "tools/list":
        result = {{"tools": [{{"name": k, "inputSchema": v}}
                           for k, v in schemas.items()] if mode != "schema" else []}}
    elif method == "tools/call":
        name = request["params"]["name"]
        if name == "session_status" and request["params"].get("arguments"):
            name = "unknown_option"
        result = {{"content": [{{"type": "text", "text": "{{}}"}}],
                   "isError": name != "session_status"}}
        if mode == name:
            result["isError"] = not result["isError"]
    else:
        raise RuntimeError(method)
    print(json.dumps({{"jsonrpc": "2.0", "id": request["id"], "result": result}}),
          flush=True)
'''


class SmokeStdioTests(unittest.TestCase):
    def test_optimized_acceptance_rejects_bad_responses(self):
        schemas = (ROOT / "tests/data/tool_schemas.json").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="stdio stub ") as directory:
            entry = Path(directory) / "stub.py"
            for mode in ("valid", "schema", "version", "session_status",
                         "transfer_modeler_to_stability", "surface_summary", "unknown_option"):
                with self.subTest(mode=mode):
                    entry.write_text(STUB.format(schemas=schemas, mode=mode), encoding="utf-8")
                    result = subprocess.run(
                        [sys.executable, "-O", str(ROOT / "scripts/smoke_stdio.py"),
                         "--python", sys.executable, "--entry", str(entry)],
                        capture_output=True, text=True, timeout=30)
                    if mode == "valid":
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(json.loads(result.stdout)["raw_input_validation"], "pass")
                    else:
                        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertNotIn('"pass"', result.stdout)


if __name__ == "__main__":
    unittest.main()
