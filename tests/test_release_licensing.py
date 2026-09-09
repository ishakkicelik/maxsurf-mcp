"""Release regressions: licensing drift must stop packaging before output."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("release_licensing_tests_policy",ROOT/"scripts/release_licensing.py")
policy=importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

class ReleaseLicensingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        names=["LICENSE","README.md","pyproject.toml","requirements.txt","requirements.lock","server.py",
               "SECURITY.md","THIRD_PARTY_NOTICES.md","CHANGELOG.md",".gitignore",
               *policy.MANIFESTS,"plugins/maxsurf-mcp/LICENSE","plugins/maxsurf-mcp/README.md",
               "scripts/build_release.py","scripts/release_licensing.py"]
        for rel in names:
            target=self.root/rel
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/rel,target)
    def check(self):
        return policy.validate_licenses(self.root,(p for p in self.root.rglob("*") if p.is_file()))
    def test_authorized_notice_and_metadata_are_accepted(self):
        report=self.check()
        self.assertEqual(report["project_license"],"MIT")
        self.assertEqual(report["first_party_license_files"],2)
    def test_missing_or_changed_grant_is_rejected(self):
        for rel in policy.LICENSES:
            with self.subTest(rel=rel):
                path=self.root/rel
                original=path.read_bytes()
                path.write_text("MIT License\nCopyright (c) 2026 Someone Else\n",encoding="utf8")
                with self.assertRaisesRegex(ValueError,"license|LICENSE"):
                    self.check()
                path.write_bytes(original)
    def test_each_client_manifest_must_use_mit(self):
        for rel in policy.MANIFESTS:
            with self.subTest(rel=rel):
                path=self.root/rel
                original=path.read_bytes()
                value=json.loads(original)
                value["license"]="Proprietary"
                path.write_text(json.dumps(value),encoding="utf8")
                with self.assertRaisesRegex(ValueError,"Manifest must declare MIT"):
                    self.check()
                path.write_bytes(original)
    def test_additional_conflicting_license_is_rejected(self):
        path=self.root/"docs/LICENSE.txt"
        path.parent.mkdir()
        path.write_text("All rights reserved.\n",encoding="utf8")
        with self.assertRaisesRegex(ValueError,"additional first-party license"):
            self.check()
    def test_metadata_and_readme_drift_are_rejected(self):
        for rel,old,new in [
            ("pyproject.toml",'file = "LICENSE"','text = "Proprietary"'),
            ("README.md","License-MIT-","License-Custom-"),
        ]:
            with self.subTest(rel=rel):
                path=self.root/rel
                original=path.read_bytes()
                text=original.decode("utf8")
                self.assertIn(old,text)
                path.write_text(text.replace(old,new),encoding="utf8")
                with self.assertRaises(ValueError):
                    self.check()
                path.write_bytes(original)
    def test_build_refuses_conflict_before_creating_an_artifact(self):
        (self.root/"LICENSE").write_text("All rights reserved.",encoding="utf8")
        out=self.root/"dist/refused"
        result=subprocess.run([sys.executable,str(self.root/"scripts/build_release.py"),
                               "--source-only","--output-dir",str(out)],
                              capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("owner-authorized MIT",result.stderr)
        self.assertFalse(out.exists())

if __name__=="__main__":unittest.main()
