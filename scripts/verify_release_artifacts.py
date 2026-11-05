"""Verify release identity, then exercise freshly relocated MCPB/Codex servers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_release import ROOT, source_files


def extract(archive, destination):
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(set(names)) != len(names):
            raise RuntimeError(f"Duplicate archive members: {archive.name}")
        for name in names:
            path = PurePosixPath(name)
            if (not path.parts or path.is_absolute() or ".." in path.parts
                    or "\\" in name or ":" in name
                    or (bundle.getinfo(name).external_attr >> 16) & 0o170000 == 0o120000):
                raise RuntimeError(f"Unsafe archive member: {name}")
        bad = bundle.testzip()
        if bad:
            raise RuntimeError(f"ZIP integrity failure: {archive.name}:{bad}")
        bundle.extractall(destination)


def identical(source, target):
    if not target.is_file() or source.read_bytes() != target.read_bytes():
        raise RuntimeError(f"Source identity differs or file missing: {target}")


def verify(artifact_dir, python):
    artifact_dir = Path(artifact_dir).resolve()
    python = str(Path(shutil.which(python) or python).resolve())
    version = json.loads((ROOT / "extension/manifest.json").read_text())["version"]
    names = (f"maxsurf-mcp-{version}-source.zip",
             f"maxsurf-mcp-{version}-win-x64-py312.mcpb",
             f"maxsurf-mcp-{version}-codex-win-x64-py312.zip")
    checksum_files = list(artifact_dir.glob("SHA256SUMS-*.json"))
    if len(checksum_files) != 1:
        raise RuntimeError("Expected exactly one SHA256SUMS manifest")
    checksums = json.loads(checksum_files[0].read_text(encoding="utf-8"))
    if set(checksums) != set(names):
        raise RuntimeError("Checksum manifest does not name exactly the three release archives")
    for name in names:
        if hashlib.sha256((artifact_dir / name).read_bytes()).hexdigest() != checksums[name]:
            raise RuntimeError(f"Checksum mismatch: {name}")
    with tempfile.TemporaryDirectory(prefix="maxsurf relocated release ") as temporary:
        relocation = Path(temporary)
        source, bundle, codex = (relocation / name for name in ("source", "mcpb", "codex"))
        for name, destination in zip(names, (source, bundle, codex)):
            extract(artifact_dir / name, destination)
        paths = list(source_files())
        expected = {path.relative_to(ROOT).as_posix() for path in paths}
        actual = {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()}
        if actual != expected:
            raise RuntimeError("Source archive file set differs from checkout")
        plugin = codex / "plugins/maxsurf-mcp"
        for path in paths:
            relative = path.relative_to(ROOT)
            identical(path, source / relative)
            if relative.parts[0] == "maxsurf_mcp" or relative.as_posix() == "server.py":
                identical(path, bundle / "server" / relative)
                identical(path, plugin / "server" / relative)
            if relative.parts[:2] == ("plugins", "maxsurf-mcp"):
                identical(path, codex / relative)
            if relative.parts[0] == "docs" or relative.as_posix() in {
                    "LICENSE", "THIRD_PARTY_NOTICES.md", "SECURITY.md", "README.md"}:
                identical(path, bundle / relative)
                identical(path, codex / relative)
            if relative.as_posix() in {"LICENSE", "THIRD_PARTY_NOTICES.md", "SECURITY.md"}:
                identical(path, plugin / relative)
            if relative.as_posix() == "extension/icon.png":
                identical(path, bundle / "icon.png")
        identical(source / "extension/manifest.json", bundle / "manifest.json")
        identical(source / ".agents/plugins/marketplace.json",
                  codex / ".agents/plugins/marketplace.json")
        for server in (bundle / "server", plugin / "server"):
            identical(source / "extension/server/launch.py", server / "launch.py")
            if not (server / "lib/mcp/__init__.py").is_file():
                raise RuntimeError(f"Bundled MCP dependency missing: {server}")
        bundle_lib = bundle / "server/lib"
        plugin_lib = plugin / "server/lib"
        libraries = {p.relative_to(bundle_lib) for p in bundle_lib.rglob("*") if p.is_file()}
        if libraries != {p.relative_to(plugin_lib) for p in plugin_lib.rglob("*") if p.is_file()}:
            raise RuntimeError("Bundled dependency file sets differ")
        for relative in libraries:
            identical(bundle_lib / relative, plugin_lib / relative)
        unrelated = relocation / "unrelated working directory"
        unrelated.mkdir()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("PYTHON", "MAXSURF_MCP_"))}
        env.update(MAXSURF_MCP_RUNTIME_DIR=str(relocation / "runtime"),
                   MAXSURF_MCP_AUDIT_DIR=str(relocation / "audit"),
                   MAXSURF_MCP_GEN_PY_DIR=str(relocation / "generated wrappers"),
                   MAXSURF_MCP_WORKSPACE_ROOTS=str(unrelated),
                   MAXSURF_MCP_ENABLE_DIAGNOSTICS="0", MAXSURF_MCP_ENABLE_LEGACY="0")
        smoke = source / "scripts/smoke_stdio.py"
        for arguments in (
                ["--python", python, "--entry", str(bundle / "server/launch.py"), "--bundle"],
                ["--plugin-root", str(plugin)]):
            subprocess.run([python, "-B", str(smoke), *arguments], cwd=unrelated,
                           env=env, check=True, timeout=120)
    print(json.dumps({"archives": 3, "checksums": "pass", "source_identity": "pass",
                      "relocated_mcpb": "pass", "relocated_codex": "pass"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    verify(args.artifact_dir, args.python)


if __name__ == "__main__":
    main()
