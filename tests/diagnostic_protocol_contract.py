"""Enabled diagnostic contracts, executed by the offline suite's subprocess."""
from unittest import TestCase

from tests.test_protocol_validation import DiagnosticProtocolContract


class DiagnosticProtocolTests(DiagnosticProtocolContract, TestCase):
    """Run every diagnostic assertion with registration enabled at import time."""
