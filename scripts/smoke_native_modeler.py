"""Exercise MCP geometry/files on a copy of an open, saved disposable test ship."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import uuid

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def snapshot(call: Any) -> dict[str, Any]:
    """Read complete preservation evidence, including non-geometry document data."""
    connection = await call("connect_maxsurf", {"module": "modeler"})
    session = connection["session"]
    state = {"identity": {key: session.get(key) for key in (
        "design_path", "module", "progid", "clsid", "com_host_pid",
    )}}
    for name in ("list_surfaces", "list_meshes", "list_markers",
                 "list_modeler_grid", "get_frame_of_reference"):
        arguments = {"limit": 5000} if name in ("list_markers", "list_modeler_grid") else {}
        state[name] = await call(name, arguments)
    state["control_nets"] = [
        await call("get_control_net", {"index": surface["index"]})
        for surface in state["list_surfaces"]["surfaces"]
    ]
    state["mesh_statistics"] = [
        await call("get_mesh_statistics", {"index": index})
        for index in range(1, state["list_meshes"]["count"] + 1)
    ]
    def require_readable(value: Any) -> None:
        if isinstance(value, dict):
            if "status" in value and value["status"] not in ("read", "ok"):
                raise RuntimeError(f"Incomplete preservation getter: {value['status']}")
            for key, item in value.items():
                if key.endswith("_status") and item != "read":
                    raise RuntimeError(f"Incomplete preservation getter: {key}={item}")
                require_readable(item)
        elif isinstance(value, list):
            for item in value:
                require_readable(item)

    for name in ("list_markers", "list_modeler_grid", "get_frame_of_reference"):
        require_readable(state[name])
    if state["list_markers"]["truncated"] or any(
        grid["truncated"] for grid in state["list_modeler_grid"]["grids"].values()
    ):
        raise RuntimeError("Incomplete preservation snapshot; no safe restoration comparison")
    return state


async def run(python: str, entry: str, workspace: Path,
              disposable_fixture: Path | None = None,
              confirm_saved_disposable_fixture: bool = False) -> None:
    """Only edit a copy of an explicitly attested saved test fixture already open.

    Modeler exposes no proven dirty-state readback here. The confirmation is a
    user assertion that the fixture on disk is the complete current document,
    not permission to save an arbitrary open design.
    """
    target = workspace.resolve() / ("native-smoke-" + uuid.uuid4().hex)
    target.mkdir(parents=True)
    report: dict[str, Any] = {
        "status": "running", "workspace": str(target), "calls": [],
        "initial_state": None, "final_state": None,
        "cleanup": {"status": "not_needed", "manual_recovery": None},
    }
    fixture = disposable_fixture.resolve() if disposable_fixture else None
    roots = {
        "MAXSURF_MCP_RUNTIME_DIR": str(target / "runtime"),
        "MAXSURF_MCP_AUDIT_DIR": str(target / "audit"),
        "MAXSURF_MCP_GEN_PY_DIR": str(target / "gen_py"),
    }
    report["output_roots"] = roots
    parameters = StdioServerParameters(
        command=python,
        args=[str(Path(entry).resolve())],
        env=dict(os.environ, **roots,
                 MAXSURF_MCP_WORKSPACE_ROOTS=os.pathsep.join(
                     [str(target)] + ([str(fixture.parent)] if fixture else [])),
                 MAXSURF_MCP_ATTACH_POLICY="activate_existing",
                 MAXSURF_MCP_ENABLE_DIAGNOSTICS="0",
                 MAXSURF_MCP_ENABLE_LEGACY="0"),
    )
    try:
        if not confirm_saved_disposable_fixture or fixture is None:
            raise RuntimeError(
                "Restoration cannot be guaranteed: supply --disposable-fixture "
                "and --confirm-saved-disposable-fixture; unsaved designs are unsupported"
            )
        if fixture.suffix.lower() != ".msd" or not fixture.is_file():
            raise RuntimeError("Disposable fixture must be an existing saved .msd file")
        fixture_bytes = fixture.read_bytes()
        if not fixture_bytes:
            raise RuntimeError("Disposable fixture is empty")
        report["fixture"] = {"path": str(fixture),
                             "sha256": hashlib.sha256(fixture_bytes).hexdigest()}
        recovery = target / "original-fixture.msd"
        recovery.write_bytes(fixture_bytes)
        report["fixture"]["recovery_copy"] = str(recovery)
        working = target / "working-fixture.msd"
        working.write_bytes(fixture_bytes)
        report["fixture"]["working_copy"] = str(working)
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                initialization = await session.initialize()
                report["server"] = initialization.serverInfo.model_dump(mode="json")

                async def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                    result = await session.call_tool(name, arguments)
                    report["calls"].append({
                        "tool": name, "arguments": arguments,
                        "response": result.model_dump(mode="json"),
                    })
                    if result.isError:
                        raise RuntimeError(f"Native tool {name} failed: {result.content}")
                    text = next(part.text for part in result.content if part.type == "text")
                    payload = json.loads(text)
                    print(f"NATIVE_TOOL {name} PASS", flush=True)
                    return payload

                initial = await snapshot(call)
                report["initial_state"] = initial
                report["final_state"] = initial
                identity_path = initial["identity"]["design_path"]
                if not identity_path or Path(identity_path).resolve() != fixture:
                    raise RuntimeError("Open document identity is not the supplied disposable fixture")
                if fixture.read_bytes() != fixture_bytes:
                    raise RuntimeError("Fixture file changed before mutation")
                try:
                    await call("open_design", {
                        "path": str(working), "save_current": False,
                        "confirm_discard": True,
                    })
                    copied = await snapshot(call)
                    report["working_initial_state"] = copied
                    expected_copy = dict(initial, identity=dict(
                        initial["identity"], design_path=str(working),
                    ))
                    if copied != expected_copy:
                        raise RuntimeError("Saved fixture copy differs from initial document; no geometry edits attempted")
                    await exercise(call, target)
                except BaseException as exercise_error:
                    report["exercise_error"] = f"{type(exercise_error).__name__}: {exercise_error}"
                    raise
                finally:
                    # Shield recovery from cancellation as well as tool failures.
                    with anyio.CancelScope(shield=True):
                        try:
                            current = await call("connect_maxsurf", {"module": "modeler"})
                            current_session = current["session"]
                            current_path = current_session.get("design_path")
                            if not current_path or Path(current_path).resolve() not in (
                                fixture, working, target / "round-trip.msd",
                            ) or any(
                                current_session.get(key) != initial["identity"][key]
                                for key in ("module", "progid", "clsid", "com_host_pid")
                            ):
                                raise RuntimeError("Document/application identity changed; refusing to discard it")
                            if fixture.read_bytes() != fixture_bytes:
                                raise RuntimeError("Fixture file changed during smoke; refusing to load changed data")
                            await call("open_design", {
                                "path": str(fixture), "save_current": False,
                                "confirm_discard": True,
                            })
                            final = await snapshot(call)
                            report["final_state"] = final
                            if final != initial:
                                raise RuntimeError("Restored identity/geometry/markers/grids/frame differ")
                            if fixture.read_bytes() != fixture_bytes:
                                raise RuntimeError("Original fixture file changed during restoration")
                            report["cleanup"]["status"] = "restored"
                        except BaseException as cleanup_error:
                            report["cleanup"] = {
                                "status": "manual_recovery_required",
                                "error": f"{type(cleanup_error).__name__}: {cleanup_error}",
                                "manual_recovery": (
                                    f"Keep {target}. In Modeler discard only the disposable "
                                    f"smoke document and reopen {fixture}; if that file changed, "
                                    f"use {recovery}. Inspect markers, grids and frame against "
                                    "initial_state in evidence.json. No application was closed."
                                ),
                            }
                            try:
                                report["final_state"] = await snapshot(call)
                            except BaseException as read_error:
                                report["final_state"] = {"unavailable": str(read_error)}
                            raise
                report["status"] = "pass"
                report["limits"] = [
                    "Saved disposable fixture and absence of unsaved changes are explicitly user-attested.",
                    "Snapshot equality covers identity, surface control nets, mesh summaries/statistics, markers, grids and frame, not all native document data.",
                    "No solver, regulatory, fairness or independent numerical acceptance.",
                    "Original fixture remains open; application is never closed and evidence is retained.",
                ]
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        if report["final_state"] is None:
            report["final_state"] = {"unavailable": "Refused before native mutation"}
        raise
    finally:
        (target / "evidence.json").write_text(
            json.dumps(report, indent=2, allow_nan=False), encoding="utf-8",
        )
        print(f"NATIVE_EVIDENCE {target / 'evidence.json'}", flush=True)
    print("NATIVE_PASS", flush=True)


async def exercise(call: Any, target: Path) -> None:
    """Run the existing geometry/file smoke inside the restoration boundary."""
    enums = await call("get_mesh_enums", {})
    primitive = await call("add_quadrilateral_surface", {
        "name": "MCP disposable panel", "symmetry": "msSSTAsymmetric",
        "corners_json": json.dumps({
            "bottom_right": [4.0, 0.0, 0.0],
            "bottom_left": [0.0, 0.0, 0.0],
            "top_left": [0.0, 2.0, 0.0],
            "top_right": [4.0, 2.0, 0.0],
        }),
    })
    index = primitive["created_index"]
    net = await call("get_control_net", {"index": index})
    point = net["points"][0]
    await call("set_control_point", {
        "index": index, "row": point["row"], "column": point["column"],
        "longitudinal": point["longitudinal"] + 0.125,
        "offset": point["offset"], "vertical": point["vertical"],
    })
    await call("move_surface", {
        "index": index, "x": 0.25, "y": 0.0, "z": 0.0,
    })
    await call("get_surface_area_properties", {"index": index})
    await call("get_surface_linked_geometry", {"index": index})
    generated = await call("mesh_surface_parametric", {
        "name": "MCP disposable mesh", "surface_index": index,
        "longitudinal_triangles": 4, "transverse_triangles": 4,
        "diagonal_layout": next(iter(enums["msMeshDiagonalLayoutType"])),
    })
    mesh_index = generated["mesh"]["index"]
    await call("get_mesh_statistics", {"index": mesh_index})
    await call("move_mesh", {
        "index": mesh_index, "x": 0.25, "y": 0.0, "z": 0.0,
    })
    for format, suffix in (("iges", ".igs"), ("3dm", ".3dm"), ("obj", ".obj")):
        await call("export_modeler_file", {
            "path": str(target / ("geometry" + suffix)), "format": format,
        })
    await call("import_modeler_file", {
        "path": str(target / "geometry.obj"), "format": "obj",
        "confirm_replace": True, "acknowledge_unverified_semantics": True,
    })
    saved = str(target / "round-trip.msd")
    await call("save_design", {"path": saved})
    before = await call("list_surfaces", {})
    before_meshes = await call("list_meshes", {})
    await call("open_design", {
        "path": saved, "save_current": False, "confirm_discard": True,
    })
    after = await call("list_surfaces", {})
    after_meshes = await call("list_meshes", {})
    if before != after or before_meshes != after_meshes:
        raise RuntimeError("Native save/reopen summaries differ")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--entry", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--confirm-empty-design", action="store_true")
    parser.add_argument("--disposable-fixture", type=Path, required=True,
                        help="Existing saved .msd test ship/fixture already open in Modeler; populated geometry is supported")
    parser.add_argument("--confirm-saved-disposable-fixture", action="store_true",
                        help="Assert the open fixture has no unsaved changes and may be discarded")
    args = parser.parse_args()
    if not args.confirm_saved_disposable_fixture:
        parser.error("--confirm-saved-disposable-fixture is required")
    anyio.run(run, args.python, args.entry, args.workspace,
              args.disposable_fixture, args.confirm_saved_disposable_fixture)
