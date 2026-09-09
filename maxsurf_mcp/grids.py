"""Frame, grid, and XML result-grid integration boundary."""

from __future__ import annotations

from typing import Any

from . import com
from . import constants as comconstants
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfValidationError
from .guard import guarded, register


STABILITY_XMLGRID_PROGID = "BentleyStability.XMLGrid"


def _indexed_text(obj: Any, member_name: str, index: int) -> str:
    """Read one indexed COM string property without assuming projection style."""
    member = getattr(obj, member_name)
    value = member(index) if callable(member) else member[index]
    return "" if value is None else str(value)


def _column_attempt(grid: Any, count: int, index_base: int) -> dict[str, Any]:
    """Read a complete XMLGrid column range for one candidate index base."""
    columns: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for offset in range(count):
        index = index_base + offset
        try:
            columns.append({
                "index": index,
                "heading": _indexed_text(grid, "ColumnHeading", index),
                "units": _indexed_text(grid, "ColumnUnits", index),
            })
        except Exception as exc:  # noqa: BLE001 - invalid base is expected
            errors.append({"index": index, "error": str(exc)})
    return {
        "index_base": index_base,
        "columns": columns,
        "errors": errors,
        "complete": not errors and len(columns) == count,
    }


@guarded(classification=Classification.READ, module="grids",
         channel=Channel.MCP_COM)
def get_stability_loadcase_table_headers() -> str:
    """READ-ONLY. Return live Stability load-case headings and unit labels.

    Uses the registered Bentley Stability XMLGrid coclass and the proven
    ``hmInputCurrentLoadcase`` table enum. ``SetCurrentGrid`` configures only
    the temporary XMLGrid reader; no load case or design value is written.
    Both candidate COM index bases are returned so the helper never guesses
    whether this Stability build exposes XMLGrid columns as 0- or 1-based.
    """
    app = com.connect("stability")
    discovered = comconstants.discover(app)
    table_value = discovered["constants"].get("hmInputCurrentLoadcase")
    if not isinstance(table_value, int) or isinstance(table_value, bool):
        raise MaxsurfValidationError(
            "the hmInputCurrentLoadcase constant could not be resolved",
            constant_source=discovered.get("source"),
            generated_modules=discovered.get("generated_modules"),
        )

    grid = com.connect(
        "stability_xmlgrid",
        progid=STABILITY_XMLGRID_PROGID,
    )
    grid.SetCurrentGrid(table_value)
    count = int(grid.GetNoOfColumns())
    attempts = [_column_attempt(grid, count, base) for base in (0, 1)]
    complete = [attempt for attempt in attempts if attempt["complete"]]

    caption = None
    try:
        caption = str(grid.TableCaption)
    except Exception:  # noqa: BLE001 - caption is supplementary
        pass

    selected = complete[0] if len(complete) == 1 else None
    return com.to_json({
        "module": "stability",
        "source": STABILITY_XMLGRID_PROGID,
        "table_enum": {
            "name": "hmInputCurrentLoadcase",
            "value": table_value,
        },
        "caption": caption,
        "column_count": count,
        "selected": selected,
        "index_attempts": attempts,
        "selection_note": (
            "unique complete index range"
            if selected is not None
            else "inspect attempts; no index base was guessed"
        ),
    })


@guarded(classification=Classification.READ, module="grids",
         channel=Channel.MCP_COM)
def get_results_table(module: str, table: str = "") -> str:
    """READ-ONLY. Read an analysis result table, preferring XMLGrid when present.

    Candidate member names are tried in order; none of them is proven on the
    Application object, so a failure here is expected on builds that expose the
    XMLGrid coclass separately rather than as an application property.
    """
    app = com.connect(module)
    attempts = []
    for name in ("XMLGrid", "ResultsGrid", "Grid", "Tables"):
        try:
            grid = getattr(app, name)
            data = grid(table) if table and callable(grid) else grid
            if hasattr(data, "XML"):
                return com.to_json({
                    "table": table, "via": name, "xml": str(data.XML)[:20000],
                })
            return com.to_json({
                "table": table, "via": name, "data": str(data)[:20000],
            })
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"{name}: {exc}")
    raise MaxsurfCOMError(
        "no result-table member is reachable on this Application object",
        module=module,
        table=table or None,
        attempted=attempts,
        hint="this build may expose the XMLGrid coclass separately; see "
             "get_stability_loadcase_table_headers for that pattern",
    )


TOOLS = (get_stability_loadcase_table_headers, get_results_table)


def register_tools(mcp: Any) -> list[str]:
    """Register the read-only result-grid tools."""
    return register(mcp, TOOLS)
