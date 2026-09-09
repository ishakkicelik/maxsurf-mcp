"""Tests that hull summaries never present unproven values as conclusions."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from maxsurf_mcp import hydrostatics
from maxsurf_mcp.errors import MaxsurfConnectionError, MaxsurfValidationError

from .support import ComFake, IsolatedEnvTestCase


def _app(**design_members) -> ComFake:
    return ComFake(Design=ComFake(Surfaces=ComFake(Count=2), **design_members))






if __name__ == "__main__":
    unittest.main()
