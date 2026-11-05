"""Small archives exercise verification without building or installing packages."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import verify_release_artifacts as verifier
from tests.test_smoke_stdio import ROOT, STUB


class VerifyReleaseArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="artifact tests ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "checkout"
        self.artifacts = Path(self.temporary.name) / "artifacts"
        self.artifacts.mkdir()
        schemas = (ROOT / "tests/data/tool_schemas.json").read_text(encoding="utf-8")
        self.files = {
            "server.py": "# fixture\n",
            "maxsurf_mcp/__init__.py": "# fixture\n",
            "extension/manifest.json": json.dumps({"version": "fixture"}),
            "extension/server/launch.py": STUB.format(schemas=schemas, mode="valid"),
            "scripts/smoke_stdio.py": (ROOT / "scripts/smoke_stdio.py").read_text(encoding="utf-8"),
            "tests/data/tool_schemas.json": schemas,
            ".agents/plugins/marketplace.json": "{}",
            "plugins/maxsurf-mcp/.mcp.json": json.dumps({"mcpServers": {"maxsurf": {
                "command": sys.executable, "args": ["-I", "-S", "server/launch.py"], "cwd": "."}}}),
        }
        for name, text in self.files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="")
        self.bundle = {
            "server/server.py": self.files["server.py"],
            "server/maxsurf_mcp/__init__.py": self.files["maxsurf_mcp/__init__.py"],
            "server/launch.py": self.files["extension/server/launch.py"],
            "manifest.json": self.files["extension/manifest.json"],
            "server/lib/mcp/__init__.py": "# dependency fixture\n",
        }
        self.plugin = {"plugins/maxsurf-mcp/" + k: v
                       for k, v in self.bundle.items() if k.startswith("server/")}
        self.plugin["plugins/maxsurf-mcp/.mcp.json"] = self.files["plugins/maxsurf-mcp/.mcp.json"]
        self.plugin[".agents/plugins/marketplace.json"] = "{}"
        self.root_patch = patch.object(verifier, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.files_patch = patch.object(verifier, "source_files",
                                       side_effect=lambda: [self.root / name for name in self.files])
        self.files_patch.start()
        self.addCleanup(self.files_patch.stop)

    def archives(self):
        contents = (("maxsurf-mcp-fixture-source.zip", self.files),
                    ("maxsurf-mcp-fixture-win-x64-py312.mcpb", self.bundle),
                    ("maxsurf-mcp-fixture-codex-win-x64-py312.zip", self.plugin))
        checksums = {}
        for name, files in contents:
            path = self.artifacts / name
            with zipfile.ZipFile(path, "w") as archive:
                for member, text in files.items():
                    archive.writestr(member, text)
            checksums[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.artifacts / "SHA256SUMS-fixture.json").write_text(json.dumps(checksums))

    def test_real_relocated_bundle_and_plugin_protocol(self):
        self.archives()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            verifier.verify(self.artifacts, sys.executable)
        self.assertEqual(json.loads(output.getvalue())["relocated_codex"], "pass")

    def test_missing_dependency_and_changed_source_fail_before_launch(self):
        for failure in ("dependency", "source", "plugin"):
            with self.subTest(failure=failure):
                bundle = dict(self.bundle)
                plugin = dict(self.plugin)
                if failure == "dependency":
                    del self.bundle["server/lib/mcp/__init__.py"]
                elif failure == "source":
                    self.bundle["server/server.py"] = "# wrong source"
                else:
                    self.plugin["plugins/maxsurf-mcp/.mcp.json"] = "{}"
                self.archives()
                with patch.object(verifier.subprocess, "run") as launch:
                    with self.assertRaises(RuntimeError):
                        verifier.verify(self.artifacts, sys.executable)
                    launch.assert_not_called()
                self.bundle, self.plugin = bundle, plugin

    def test_checksum_tamper_and_bad_zip_fail(self):
        self.archives()
        archive = self.artifacts / "maxsurf-mcp-fixture-source.zip"
        archive.write_bytes(b"not a zip")
        with self.assertRaisesRegex(RuntimeError, "Checksum mismatch"):
            verifier.verify(self.artifacts, sys.executable)
        sums = self.artifacts / "SHA256SUMS-fixture.json"
        checksums = json.loads(sums.read_text())
        checksums[archive.name] = hashlib.sha256(archive.read_bytes()).hexdigest()
        sums.write_text(json.dumps(checksums))
        with self.assertRaises(zipfile.BadZipFile):
            verifier.verify(self.artifacts, sys.executable)

    def test_unsafe_archive_member_is_refused(self):
        archive = self.artifacts / "unsafe.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("../outside", "bad")
        with self.assertRaisesRegex(RuntimeError, "Unsafe archive member"):
            verifier.extract(archive, self.root)

    def test_duplicate_and_crc_corrupt_members_are_refused(self):
        archive = self.artifacts / "integrity.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("member", "unique payload")
        archive.write_bytes(archive.read_bytes().replace(b"unique payload", b"broken payload"))
        with self.assertRaisesRegex(RuntimeError, "ZIP integrity"):
            verifier.extract(archive, self.root)
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("first", "one")
            output.writestr("other", "two")
        archive.write_bytes(archive.read_bytes().replace(b"other", b"first"))
        with self.assertRaisesRegex(RuntimeError, "Duplicate archive"):
            verifier.extract(archive, self.root)

    def test_broken_plugin_relative_command_fails_real_launch(self):
        definition = {"mcpServers": {"maxsurf": {
            "command": sys.executable, "args": ["-I", "-S", "missing/launch.py"], "cwd": "."}}}
        self.files["plugins/maxsurf-mcp/.mcp.json"] = json.dumps(definition)
        (self.root / "plugins/maxsurf-mcp/.mcp.json").write_text(json.dumps(definition))
        self.plugin["plugins/maxsurf-mcp/.mcp.json"] = json.dumps(definition)
        self.archives()
        with self.assertRaises(subprocess.CalledProcessError):
            verifier.verify(self.artifacts, sys.executable)


if __name__ == "__main__":
    unittest.main()
