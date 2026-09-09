"""Build allowlisted source ZIP and a Windows CPython 3.12 MCPB.

Run from an environment with pip. Dependencies come from pinned requirements;
the caller's source checkout, virtualenv and private runtime are not bundled.
No signing or publication is performed.
"""
from __future__ import annotations
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import uuid
import zipfile

from release_licensing import validate_licenses

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {"server.py", "pyproject.toml", "requirements.txt", "requirements.lock",
              "README.md", "LICENSE", "SECURITY.md", "THIRD_PARTY_NOTICES.md",
              "CHANGELOG.md", ".gitignore"}
SOURCE_DIRS = ("maxsurf_mcp", "tests", "scripts", "docs", "extension", ".github", "plugins", ".agents")
SUFFIXES = {".py", ".json", ".toml", ".md", ".txt", ".ps1", ".yml", ".yaml", ".png"}
FORBIDDEN = {"__pycache__", ".git", "runtime", "gen_py", ".venv", "lib", "build", "dist"}


def source_files():
    for name in sorted(ROOT_FILES):
        path = ROOT / name
        if not path.is_file():
            raise RuntimeError(f"Missing required release file: {name}")
        yield path
    for directory in SOURCE_DIRS:
        for path in sorted((ROOT / directory).rglob("*")):
            relative = path.relative_to(ROOT)
            if relative.parts[:3] == ("plugins", "maxsurf-mcp", "server"):
                continue
            if not path.is_file() or any(p in FORBIDDEN for p in relative.parts):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
                raise RuntimeError(f"Source escapes release root: {relative}")
            if path.suffix.lower() not in SUFFIXES and path.name != "LICENSE":
                raise RuntimeError(f"Unreviewed source artifact: {relative}")
            yield path


def archive(target, entries):
    # Exclusive mode prevents accidentally overwriting an existing release.
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as out:
        for path, name in sorted(entries, key=lambda x: x[1]):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            out.writestr(info, path.read_bytes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "extension/manifest.json").read_text())
    version = manifest["version"]
    output = args.output_dir.resolve()
    if not output.is_relative_to(ROOT / "dist"):
        raise SystemExit("Build output must stay inside this release tree's dist directory.")
    paths = list(source_files())
    license_report = validate_licenses(ROOT, paths)
    output.mkdir(parents=True, exist_ok=True)
    artifacts = []
    source = output / f"maxsurf-mcp-{version}-source.zip"
    archive(source, [(p, p.relative_to(ROOT).as_posix()) for p in paths])
    artifacts.append(source)
    if not args.source_only:
        if sys.platform != "win32" or sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
            raise SystemExit("MCPB build requires Windows x64 CPython 3.12.")
        stage = ROOT / "build" / ("mcpb-" + uuid.uuid4().hex)
        server = stage / "server"
        server.mkdir(parents=True)
        shutil.copy2(ROOT / "extension/manifest.json", stage / "manifest.json")
        shutil.copy2(ROOT / "extension/icon.png", stage / "icon.png")
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "SECURITY.md", "README.md"):
            shutil.copy2(ROOT / name, stage / name)
        shutil.copytree(ROOT / "docs", stage / "docs", ignore=shutil.ignore_patterns("__pycache__"))
        for path in paths:
            relative = path.relative_to(ROOT)
            if relative.parts[0] == "maxsurf_mcp" or relative.as_posix() == "server.py":
                destination = server / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
        shutil.copy2(ROOT / "extension/server/launch.py", server / "launch.py")
        lib = server / "lib"
        subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                        "--only-binary=:all:", "--no-compile", "--target", str(lib),
                        "-r", str(ROOT / "requirements.lock")], check=True)
        inventory = []
        for dist in sorted(metadata.distributions(path=[str(lib)]), key=lambda d: d.metadata["Name"]):
            inventory.append({"name": dist.metadata["Name"], "version": dist.version,
                              "license": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
                              "license_files": [str(f) for f in dist.files or ()
                                                if any(x in str(f).lower() for x in ("license", "copying", "notice"))]})
        # Generated build inventory, not a source-file edit.
        (stage / "THIRD_PARTY_INVENTORY.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
        check_env = dict(os.environ, MAXSURF_MCP_RUNTIME_DIR=str(ROOT / "build" / "verification-runtime"))
        subprocess.run([sys.executable, "-I", "-S", str(server / "launch.py"), "--check"], check=True, env=check_env)
        bundle = output / f"maxsurf-mcp-{version}-win-x64-py312.mcpb"
        archive(bundle, [(p, p.relative_to(stage).as_posix()) for p in stage.rglob("*")
                         if p.is_file() and "__pycache__" not in p.parts
                         and p.suffix not in {".pyc", ".chm"}])
        artifacts.append(bundle)
        # A distinct Codex repo marketplace archive; never rename MCPB to ZIP.
        codex_root = stage.parent / ("codex-" + uuid.uuid4().hex)
        plugin = codex_root / "plugins" / "maxsurf-mcp"
        shutil.copytree(ROOT / "plugins" / "maxsurf-mcp", plugin,
                        ignore=shutil.ignore_patterns("server", "__pycache__"))
        shutil.copytree(server, plugin / "server", ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "SECURITY.md"):
            shutil.copy2(stage / name, plugin / name)
        catalog = codex_root / ".agents" / "plugins"
        catalog.mkdir(parents=True)
        shutil.copy2(ROOT / ".agents/plugins/marketplace.json", catalog / "marketplace.json")
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "SECURITY.md", "README.md", "THIRD_PARTY_INVENTORY.json"):
            shutil.copy2(stage / name, codex_root / name)
        shutil.copytree(ROOT / "docs", codex_root / "docs")
        codex_zip = output / f"maxsurf-mcp-{version}-codex-win-x64-py312.zip"
        archive(codex_zip, [(p, p.relative_to(codex_root).as_posix())
                           for p in codex_root.rglob("*") if p.is_file()
                           and "__pycache__" not in p.parts
                           and p.suffix not in {".pyc", ".chm"}])
        artifacts.append(codex_zip)
        print("CODEX_STAGING:", codex_root)
        print("STAGING:", stage)
    checksums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts}
    with (output / ("SHA256SUMS-" + uuid.uuid4().hex[:8] + ".json")).open("x", encoding="utf-8") as file:
        json.dump(checksums, file, indent=2)
    with (output / "LICENSE_AUDIT.json").open("x", encoding="utf-8") as file:
        json.dump(license_report, file, indent=2)
    print(json.dumps(checksums, indent=2))


if __name__ == "__main__":
    main()
