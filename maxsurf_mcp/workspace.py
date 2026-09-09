"""Filesystem sandbox for every tool that reads or writes a design file.

Before this module existed, ``open_design``, ``save_design`` and the export
helpers accepted any absolute path, so a single tool call could overwrite an
arbitrary file on the host. Every file-capable tool now resolves its path
through here first.

Policy:

* the caller must supply an absolute path, and ``..`` is refused outright;
* the path is fully normalised and symlinks/junctions are resolved *before*
  containment is checked, so a link cannot point out of the sandbox;
* the result must sit inside one of the configured workspace roots, which
  default to ``runtime/`` inside the repository;
* an existing target requires ``overwrite=True``;
* an optional suffix allowlist keeps design tools pointed at design files.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import config
from .errors import MaxsurfSafetyError, MaxsurfValidationError

#: Maxsurf design files. Used by the design lifecycle tools.
DESIGN_SUFFIXES = (".msd",)


def _require_text(path: str, *, purpose: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise MaxsurfValidationError(
            "a non-empty file path is required", purpose=purpose
        )
    if "\x00" in path:
        raise MaxsurfSafetyError(
            "file path contains a null byte", purpose=purpose
        )
    return path.strip()


def _reject_relative_and_parent(raw: str, *, purpose: str) -> None:
    if not os.path.isabs(raw):
        raise MaxsurfSafetyError(
            "file path must be absolute; relative paths are ambiguous "
            "and are refused",
            purpose=purpose,
            path=raw,
        )
    parts = Path(raw).parts
    if ".." in parts or any(part.strip() == ".." for part in parts):
        raise MaxsurfSafetyError(
            "parent-directory traversal is not permitted in a file path",
            purpose=purpose,
            path=raw,
        )


def _normalise(raw: str) -> Path:
    """Return a fully resolved absolute path, following links where present."""
    candidate = Path(raw)
    if candidate.exists():
        return candidate.resolve()
    # The target does not exist yet: resolve the deepest existing ancestor so
    # a symlinked parent directory cannot smuggle the target out of the root.
    parent = candidate.parent
    resolved_parent = parent
    tail = [candidate.name]
    while not resolved_parent.exists() and resolved_parent != resolved_parent.parent:
        tail.append(resolved_parent.name)
        resolved_parent = resolved_parent.parent
    resolved = resolved_parent.resolve()
    for part in reversed(tail):
        resolved = resolved / part
    return resolved


def _contained(candidate: Path, root: Path) -> bool:
    left = os.path.normcase(str(candidate))
    right = os.path.normcase(str(root))
    try:
        return os.path.commonpath([left, right]) == right
    except ValueError:
        # Different drives or mixed absolute/relative: never contained.
        return False


def _require_inside_roots(candidate: Path, *, purpose: str) -> Path:
    roots = config.workspace_roots()
    for root in roots:
        if _contained(candidate, root):
            return candidate
    raise MaxsurfSafetyError(
        "file path is outside every configured workspace root",
        purpose=purpose,
        path=str(candidate),
        workspace_roots=[str(root) for root in roots],
        hint=f"set {config.WORKSPACE_ROOTS_VAR} to allow another directory",
    )


def _require_suffix(candidate: Path, allowed: tuple[str, ...] | None,
                    *, purpose: str) -> None:
    if not allowed:
        return
    if candidate.suffix.casefold() not in {item.casefold() for item in allowed}:
        raise MaxsurfSafetyError(
            "file extension is not allowed for this operation",
            purpose=purpose,
            path=str(candidate),
            allowed_suffixes=list(allowed),
        )


def resolve_existing(path: str, *, purpose: str,
                     allowed_suffixes: tuple[str, ...] | None = None) -> Path:
    """Validate a path that must already exist and be readable."""
    raw = _require_text(path, purpose=purpose)
    _reject_relative_and_parent(raw, purpose=purpose)
    candidate = _normalise(raw)
    _require_inside_roots(candidate, purpose=purpose)
    _require_suffix(candidate, allowed_suffixes, purpose=purpose)
    if not candidate.exists():
        raise MaxsurfValidationError(
            "file does not exist", purpose=purpose, path=str(candidate)
        )
    if not candidate.is_file():
        raise MaxsurfValidationError(
            "path is not a file", purpose=purpose, path=str(candidate)
        )
    return candidate


def resolve_target(path: str, *, purpose: str, overwrite: bool,
                   allowed_suffixes: tuple[str, ...] | None = None,
                   create_parents: bool = True) -> Path:
    """Validate a write target and enforce the explicit-overwrite rule."""
    raw = _require_text(path, purpose=purpose)
    _reject_relative_and_parent(raw, purpose=purpose)
    candidate = _normalise(raw)
    _require_inside_roots(candidate, purpose=purpose)
    _require_suffix(candidate, allowed_suffixes, purpose=purpose)
    if candidate.is_dir():
        raise MaxsurfValidationError(
            "write target is an existing directory",
            purpose=purpose,
            path=str(candidate),
        )
    if candidate.exists() and not overwrite:
        raise MaxsurfSafetyError(
            "write target already exists; pass overwrite=True to replace it",
            purpose=purpose,
            path=str(candidate),
        )
    if create_parents:
        parent = candidate.parent
        _require_inside_roots(parent, purpose=purpose)
        parent.mkdir(parents=True, exist_ok=True)
    return candidate


def describe() -> dict[str, object]:
    """Return the effective sandbox configuration for diagnostics."""
    return {
        "workspace_roots": [str(root) for root in config.workspace_roots()],
        "design_suffixes": list(DESIGN_SUFFIXES),
    }
