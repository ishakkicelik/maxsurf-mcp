"""Verify first-party MIT licensing before creating distributable artifacts.

This policy applies to authored source files, not separately licensed dependencies.
"""
from __future__ import annotations
import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Iterable

COPYRIGHT = "Copyright (c) 2026 Ismail Hakki Celik"
MIT_NOTICE_SHA256 = "1735f1886398eb07f0ed52f5eadf7fb10c818d0c228c08d2a2e9d10978320b5b"
MIT_CLASSIFIER = "License :: OSI Approved :: MIT License"
MANIFESTS = ("extension/manifest.json", "plugins/maxsurf-mcp/.codex-plugin/plugin.json")
LICENSES = ("LICENSE", "plugins/maxsurf-mcp/LICENSE")
READMES = ("README.md", "plugins/maxsurf-mcp/README.md")

def normalized(text: str) -> str:
    return text.replace("\r\n", "\n").strip() + "\n"

def validate_licenses(root: Path, files: Iterable[Path]) -> dict:
    root = root.resolve()
    license_text = normalized((root/"LICENSE").read_text(encoding="utf-8-sig"))
    digest = hashlib.sha256(license_text.encode("utf8")).hexdigest()
    if digest != MIT_NOTICE_SHA256:
        raise ValueError("LICENSE differs from the owner-authorized MIT notice")
    for rel in LICENSES:
        path = root/rel
        if not path.is_file() or normalized(path.read_text(encoding="utf-8-sig")) != license_text:
            raise ValueError(f"Missing or conflicting first-party license: {rel}")
    for rel in MANIFESTS:
        manifest = json.loads((root/rel).read_text(encoding="utf-8-sig"))
        if manifest.get("license") != "MIT":
            raise ValueError(f"Manifest must declare MIT: {rel}")
        if manifest.get("author", {}).get("name") != "Ismail Hakki Celik":
            raise ValueError(f"Manifest copyright-holder identity differs: {rel}")
    project = tomllib.loads((root/"pyproject.toml").read_text(encoding="utf-8-sig"))["project"]
    if project.get("license") != {"file": "LICENSE"}:
        raise ValueError("Package metadata must use the canonical LICENSE file")
    classifiers = project.get("classifiers", [])
    if MIT_CLASSIFIER not in classifiers or any(
        x.startswith("License ::") and x != MIT_CLASSIFIER or x.startswith("Private ::")
        for x in classifiers
    ):
        raise ValueError("Package license/upload classifiers conflict with MIT")
    for rel in READMES:
        text = (root/rel).read_text(encoding="utf-8-sig")
        if "License-MIT-" not in text or "](LICENSE)" not in text or COPYRIGHT not in text:
            raise ValueError(f"README badge, license link or copyright notice missing: {rel}")
    checked = 0
    for path in files:
        rel = path.relative_to(root).as_posix()
        if re.fullmatch(r"(?i)(license|copying)(\.(txt|md|rst))?", path.name):
            if normalized(path.read_text(encoding="utf-8-sig")) != license_text:
                raise ValueError(f"Conflicting additional first-party license: {rel}")
            checked += 1
        if path.suffix.lower() in {".md", ".json", ".toml"}:
            text = path.read_text(encoding="utf-8-sig")
            # Vendor-product descriptions may say proprietary; those are not
            # project licensing declarations. Dependency trees are not inputs.
            if re.search(r"(?i)all[ -]rights[ -]reserved|no open.source license is granted", text):
                raise ValueError(f"Conflicting first-party declaration: {rel}")
    return {"project_license": "MIT", "copyright": COPYRIGHT,
            "license_notice_sha256": digest, "first_party_license_files": checked,
            "dependency_licenses": "preserved separately"}

if __name__ == "__main__":
    from build_release import ROOT, source_files
    print(json.dumps(validate_licenses(ROOT, source_files()), indent=2))
