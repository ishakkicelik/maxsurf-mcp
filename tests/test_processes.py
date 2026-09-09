"""Tests for read-only process inspection.

This module is small but load-bearing: it is the only evidence the server has
that Maxsurf is running, because Maxsurf does not publish its Application object
in the Running Object Table. If it silently reported "nothing is running", the
activation policy would refuse a perfectly good connection; if it silently
reported success while unable to look, the policy would activate blind. Both
directions are pinned here.

Every Windows call is faked, so no process is opened, started, or signalled.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from maxsurf_mcp import processes

MODELER = r"C:\Program Files\Bentley\Offshore\Maxsurf 2025\bin\win64\MaxsurfModeler.exe"
STABILITY = r"C:\Program Files\Bentley\Offshore\Maxsurf 2025\bin\win64\MaxsurfStability.exe"


class FakeHandle:
    """A process handle that knows which process it belongs to."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.closed = False

    def Close(self) -> None:  # noqa: N802 - Windows naming
        self.closed = True


class FakeWin32Api:
    """``win32api`` with a scripted set of openable processes."""

    def __init__(self, openable: set[int], denied: set[int] | None = None) -> None:
        self.openable = set(openable)
        self.denied = set(denied or ())
        self.handles: list[FakeHandle] = []
        self.opened: list[tuple[int, int]] = []

    def OpenProcess(self, access, _inherit, pid):  # noqa: N802 - Windows naming
        self.opened.append((int(pid), int(access)))
        if pid in self.denied or pid not in self.openable:
            raise OSError("access is denied")
        handle = FakeHandle(int(pid))
        self.handles.append(handle)
        return handle


class FakeWin32Process:
    """``win32process`` with a scripted process table."""

    def __init__(self, table: dict[int, str], enum_error: bool = False) -> None:
        self.table = dict(table)
        self.enum_error = enum_error

    def EnumProcesses(self):  # noqa: N802 - Windows naming
        if self.enum_error:
            raise OSError("enumeration failed")
        return list(self.table)

    def GetModuleFileNameEx(self, handle, _module):  # noqa: N802 - Windows naming
        return self.table[handle.pid]


class ProcessLookupTestCase(unittest.TestCase):
    def install(self, table, openable=None, denied=None, enum_error=False):
        api = FakeWin32Api(
            openable=set(table) if openable is None else set(openable),
            denied=denied,
        )
        proc = FakeWin32Process(table, enum_error=enum_error)
        for name, value in (("win32api", api), ("win32process", proc)):
            patcher = mock.patch.object(processes, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return api, proc


class FindTests(ProcessLookupTestCase):
    def test_it_finds_the_maxsurf_executables_by_image_name(self):
        self.install({6456: MODELER, 15228: STABILITY, 4: r"C:\Windows\System32\svchost.exe"})

        found = processes.find(("MaxsurfModeler.exe",))

        self.assertTrue(found["supported"])
        self.assertEqual(found["pids"], (6456,))
        self.assertEqual(found["matches"][0]["name"], "MaxsurfModeler.exe")
        self.assertEqual(found["matches"][0]["path"], MODELER)

    def test_matching_is_case_insensitive(self):
        self.install({6456: MODELER.replace("MaxsurfModeler", "MAXSURFMODELER")})
        self.assertEqual(processes.find(("maxsurfmodeler.exe",))["pids"], (6456,))

    def test_several_instances_are_all_reported_in_pid_order(self):
        self.install({9000: MODELER, 100: MODELER})
        self.assertEqual(processes.find(("MaxsurfModeler.exe",))["pids"],
                         (100, 9000))

    def test_an_absent_executable_is_reported_as_supported_but_empty(self):
        """"Looked and found nothing" must not look like "could not look"."""
        self.install({4: r"C:\Windows\System32\svchost.exe"})

        found = processes.find(("MaxsurfModeler.exe",))

        self.assertTrue(found["supported"])
        self.assertEqual(found["pids"], ())

    def test_a_process_that_cannot_be_opened_is_skipped_not_fatal(self):
        self.install({6456: MODELER, 4: r"C:\Windows\System32\smss.exe"},
                     openable={6456, 4}, denied={4})

        found = processes.find(("MaxsurfModeler.exe",))

        self.assertTrue(found["supported"])
        self.assertEqual(found["pids"], (6456,))

    def test_the_limited_access_right_is_tried_first(self):
        api, _proc = self.install({6456: MODELER})
        processes.find(("MaxsurfModeler.exe",))
        self.assertEqual(api.opened[0][1],
                         processes.PROCESS_QUERY_LIMITED_INFORMATION)

    def test_every_handle_is_closed(self):
        api, _proc = self.install({6456: MODELER, 15228: STABILITY})
        processes.find(("MaxsurfModeler.exe", "MaxsurfStability.exe"))
        self.assertTrue(api.handles)
        self.assertTrue(all(handle.closed for handle in api.handles))

    def test_no_executable_name_is_not_treated_as_nothing_running(self):
        self.install({6456: MODELER})
        found = processes.find(())
        self.assertEqual(found["pids"], ())
        self.assertIn("no executable name", found["detail"])

    def test_a_failed_enumeration_is_reported_as_unsupported(self):
        self.install({6456: MODELER}, enum_error=True)

        found = processes.find(("MaxsurfModeler.exe",))

        self.assertFalse(found["supported"])
        self.assertIn("enumeration failed", found["detail"])

    def test_it_reports_unsupported_without_pywin32(self):
        for name in ("win32api", "win32process"):
            patcher = mock.patch.object(processes, name, None)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.assertFalse(processes.available())
        found = processes.find(("MaxsurfModeler.exe",))
        self.assertFalse(found["supported"])
        self.assertEqual(found["pids"], ())

    def test_pids_of_returns_just_the_identifiers(self):
        self.install({6456: MODELER})
        self.assertEqual(processes.pids_of(("MaxsurfModeler.exe",)), (6456,))


class NoSideEffectTests(unittest.TestCase):
    def test_the_module_cannot_start_or_stop_a_process(self):
        """A read-only module must not import the means to do anything else."""
        source = Path(processes.__file__).read_text(encoding="utf-8")
        for forbidden in ("TerminateProcess", "CreateProcess", "subprocess",
                          "os.kill", "taskkill", "Dispatch"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
