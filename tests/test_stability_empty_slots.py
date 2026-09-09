"""Regression for native undefined load-case slots in a fresh Stability design."""
import unittest
from types import SimpleNamespace as NS
from maxsurf_mcp.stability import _loadcase_names
from maxsurf_mcp.errors import MaxsurfCOMError

class Cases:
    def __init__(self, rows): self.rows=rows; self.Count=len(rows)
    def __call__(self, i): return self.rows[i-1]

class Empty:
    IsDefined = False
    @property
    def Name(self): raise RuntimeError("Loadcase is not open")

class UndefinedSlotTests(unittest.TestCase):
    def test_eight_undefined_slots_are_not_read(self):
        self.assertEqual(_loadcase_names(NS(LoadCases=Cases([Empty() for _ in range(8)]))),set())
    def test_defined_names_still_prevent_replacement(self):
        d=NS(LoadCases=Cases([Empty(),NS(IsDefined=True,Name="Existing")]))
        self.assertEqual(_loadcase_names(d),{"Existing"})
    def test_unknown_definition_status_is_not_empty(self):
        with self.assertRaises(MaxsurfCOMError):
            _loadcase_names(NS(LoadCases=Cases([NS(IsDefined=None,Name="unsafe")])) )
    def test_unreadable_defined_name_fails_closed(self):
        class Broken(Empty): IsDefined=True
        with self.assertRaises(RuntimeError):_loadcase_names(NS(LoadCases=Cases([Broken()])))
