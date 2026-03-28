"""Tests for the enhanced security scanner evasion pattern detection."""
from __future__ import annotations

import pytest

from prax.services.workspace_service import _ast_scan


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _has_pattern(findings: list[dict], substring: str) -> bool:
    """Return True if any finding's pattern contains *substring*."""
    return any(substring in f["pattern"] for f in findings)


# ---------------------------------------------------------------------------
# New evasion patterns — each should be detected
# ---------------------------------------------------------------------------

class TestEvasionPatterns:

    def test_getattr_environ(self):
        code = "x = getattr(os, 'environ')"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "environ")

    def test_vars_os(self):
        code = "d = vars(os)"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "vars(os)")

    def test_os_dunder_dict(self):
        code = "d = os.__dict__"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "__dict__")

    def test_importlib_import_module(self):
        code = "importlib.import_module('os')"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "importlib.import_module")

    def test_sys_modules_subscript(self):
        code = "m = sys.modules['os']"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "sys.modules")

    def test_dunder_globals(self):
        code = "g = func.__globals__"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "__globals__")

    def test_dunder_subclasses(self):
        code = "subs = object.__subclasses__()"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "__subclasses__")

    def test_dunder_bases(self):
        code = "b = MyClass.__bases__"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "__bases__")

    def test_import_ctypes(self):
        code = "import ctypes"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "ctypes")

    def test_import_pickle(self):
        code = "import pickle"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "pickle")

    def test_import_marshal(self):
        code = "import marshal"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "marshal")

    def test_from_ctypes_import(self):
        code = "from ctypes import cdll"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "ctypes")

    def test_from_pickle_import(self):
        code = "from pickle import loads"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "pickle")

    def test_getattr_builtins(self):
        code = "fn = getattr(__builtins__, 'eval')"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "getattr(__builtins__)")


# ---------------------------------------------------------------------------
# Existing patterns still caught
# ---------------------------------------------------------------------------

class TestExistingPatterns:

    def test_import_subprocess(self):
        code = "import subprocess"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "subprocess")

    def test_from_subprocess_import(self):
        code = "from subprocess import run"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "subprocess")

    def test_eval_call(self):
        code = "eval('1+1')"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "eval()")

    def test_exec_call(self):
        code = "exec('print(1)')"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "exec()")

    def test_os_system(self):
        code = "os.system('ls')"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "os.system")

    def test_os_environ_access(self):
        code = "k = os.environ['KEY']"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "os.environ")

    def test_import_socket(self):
        code = "import socket"
        findings = _ast_scan(code)
        assert _has_pattern(findings, "socket")


# ---------------------------------------------------------------------------
# Clean code should pass
# ---------------------------------------------------------------------------

class TestCleanCode:

    def test_simple_function(self):
        code = '''
def hello(name: str) -> str:
    return f"Hello, {name}!"
'''
        findings = _ast_scan(code)
        assert findings == []

    def test_standard_imports(self):
        code = '''
import json
import os.path
from pathlib import Path
from typing import Any
'''
        findings = _ast_scan(code)
        assert findings == []

    def test_normal_class(self):
        code = '''
class MyPlugin:
    def __init__(self):
        self.data = {}
    def process(self, text):
        return text.upper()
'''
        findings = _ast_scan(code)
        assert findings == []
