"""Audit writer used as a *separate process* by the concurrency tests.

Deliberately not named ``test_*``: unittest discovery must not collect it. It is
spawned with ``sys.executable`` so that each writer holds a genuinely
independent process-local view of the chain head, which is the exact condition
that corrupted the log before interprocess locking existed.

Usage: python -m tests.audit_writer <audit-dir> <label> <count>
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv: list[str]) -> int:
    directory, label, count = argv[1], argv[2], int(argv[3])
    os.environ["MAXSURF_MCP_AUDIT"] = "1"
    os.environ["MAXSURF_MCP_AUDIT_DIR"] = directory

    from maxsurf_mcp import audit

    log = audit.AuditLog(directory=Path(directory), enabled=True)
    for index in range(count):
        log.record(
            stage="complete",
            tool=f"{label}:{index}",
            module="concurrency",
            classification=audit.Classification.READ,
            channel=audit.Channel.INTERNAL,
            outcome=audit.Outcome.SUCCESS,
            result_summary={"writer": label, "index": index},
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
