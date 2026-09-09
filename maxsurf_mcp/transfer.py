"""File-mediated module transfer, without a vessel-design recipe."""
from __future__ import annotations
import hashlib
from pathlib import Path
from . import audit, com, config, workspace
from .errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError

def transfer(module: str, *, confirm_replace: bool, overwrite: bool = False):
    if confirm_replace is not True:
        raise MaxsurfSafetyError(
            "opening a destination replaces its current state; save it first and "
            "explicitly set confirm_replace=true", destination=module)
    destination_app = com.connect(module)
    if not bool(destination_app.IsInitializedCorrectly):
        raise MaxsurfCOMError("destination automation is not initialized", module=module)
    modeler = com.connect("modeler")
    source = modeler.Design
    count = int(source.Surfaces.Count)
    if count <= 0:
        raise MaxsurfValidationError("Modeler has no surfaces")
    identity = {"path": str(source.Path), "name": str(source.Name),
                "surface_count": count}
    target = workspace.resolve_target(
        str(config.runtime_root() / "transfer" / f"{module}_{audit.new_id()}.msd"),
        purpose=f"transfer_modeler_to_{module}", overwrite=overwrite,
        allowed_suffixes=workspace.DESIGN_SUFFIXES)
    target.parent.mkdir(parents=True, exist_ok=True)
    destination = destination_app.Design
    path_member = "Path" if module == "stability" else "DesignPath"
    previous = str(getattr(destination, path_member))
    try:
        source.SaveAs(str(target), overwrite)
        if not target.is_file() or target.stat().st_size == 0:
            raise MaxsurfCOMError("Modeler did not write a nonempty transfer file")
        if module == "stability":
            destination.Open(str(target))
        else:
            destination.DesignOpen(str(target))
        actual = str(getattr(destination, path_member))
        if Path(actual).resolve() != target.resolve():
            raise MaxsurfCOMError("destination design identity mismatch",
                                  requested=str(target), actual=actual)
    finally:
        for name in ("modeler", module):
            session = com.session_for(name)
            if session is not None:
                session.refresh_design_identity()
    return com.to_json({
        "source": identity,
        "file": {"path": str(target), "bytes": target.stat().st_size,
                 "sha256": hashlib.sha256(target.read_bytes()).hexdigest()},
        "destination": {"module": module, "opened_path": actual,
                        "previous_path": previous},
        "verified": True,
        "warnings": ["SaveAs changes Modeler's active path to the transfer copy. "
                     "Destination state was replaced with explicit confirmation."]
    })
