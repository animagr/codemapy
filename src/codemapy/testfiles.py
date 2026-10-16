"""Classify source files as tests.

Test files are kept in the map, but they distort it if counted like the
program: a suite imports broadly and nothing imports it, so its importers
inflate every hub's fan-in and its files crowd the fan-out and entry-point
lists. ``is_test_file`` lets those rankings set tests aside.

Evidence, strongest first:

1. A containing directory with a test name (``tests/``, ``__tests__/``,
   ``spec/``, ``testdata/``, ``jvmTest/``, ``Foo.Tests/``, ...). The scan root
   itself never counts, because paths are project-relative.
2. A filename the toolchain or a dominant convention reserves for tests: Go's
   ``_test.go`` (enforced by the compiler), JS/TS ``.test.``/``.spec.``,
   JVM/.NET/Swift ``FooTest``/``FooTests``, Ruby ``_spec.rb``, HDL testbenches
   ``tb_*.v``/``*_tb.sv``.
3. For Python and C/C++, a test-like filename only counts when the file also
   imports a test framework. In hardware and instrument codebases a root-level
   ``test_*.py`` / ``*_test.py`` is as often the test-bench application itself,
   and a firmware ``flash_test.c`` is a production self-test.
   ``conftest.py`` is always a test file.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from codemapy.models import ImportRef

TEST_DIRECTORY_NAMES = frozenset(
    {
        "__mocks__",
        "__tests__",
        "e2e",
        "fixtures",
        "spec",
        "specs",
        "test",
        "testbench",
        "testbenches",
        "testdata",
        "tests",
        "unittests",
    }
)

# Case-sensitive directory suffixes: `jvmTest/`, `androidTest/`, `UnitTests/`,
# `Foo.Tests/`, Swift `Specs/`. Lower-case words that merely end in "test"
# (`contest/`, `latest/`) do not match.
TEST_DIRECTORY_SUFFIXES = ("Test", "Tests", "Spec", "Specs")

# Filename patterns that are test evidence on their own.
_TEST_FILENAME_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"_test\.go$",
        r"\.(?:test|spec)\.(?:[cm]?[jt]sx?|svelte)$",
        r"(?:Test|Tests)\.(?:java|kt|cs|scala)$",
        r"(?:Spec|Suite)\.(?:kt|scala)$",
        r"(?:Tests|Spec)\.swift$",
        r"Test\.php$",
        r"_(?:spec|test)\.rb$",
        r"_test\.exs$",
        r"_spec\.lua$",
        r"(?:^|/)tests\.rs$",
        r"(?:^|/)tb_[^/]+\.s?v$",
        r"_tb\.s?v$",
    )
)

# Python and C/C++ test-like filenames, which also need a framework import.
_PYTHON_TEST_FILENAME = re.compile(r"(?:^|/)(?:test_[^/]+|[^/]+_test)\.py$")
_C_TEST_FILENAME = re.compile(r"(?:^|/)(?:test_[^/]+|[^/]+_(?:test|unittest))\.(?:c|cc|cpp|cxx)$")

PYTHON_TEST_FRAMEWORKS = frozenset({"pytest", "unittest"})
C_TEST_FRAMEWORK_HEADERS = (
    "gtest/",
    "gmock/",
    "catch2/",
    "catch.hpp",
    "doctest",
    "unity.h",
    "cmocka.h",
    "CppUTest/",
    "boost/test/",
)


def is_test_file(path: str, imports: Iterable[ImportRef] = ()) -> bool:
    """Return True if the project-relative POSIX *path* is a test file.

    *imports* are the file's own import references. They corroborate a
    test-like Python or C/C++ filename (see the module docstring); without
    them, those filenames alone do not make a file a test.
    """
    if _in_test_directory(path):
        return True
    if any(pattern.search(path) for pattern in _TEST_FILENAME_PATTERNS):
        return True
    if path.rsplit("/", 1)[-1] == "conftest.py":
        return True
    if _PYTHON_TEST_FILENAME.search(path):
        return any(ref.raw.split(".", 1)[0] in PYTHON_TEST_FRAMEWORKS for ref in imports)
    if _C_TEST_FILENAME.search(path):
        return any(header in ref.raw for ref in imports for header in C_TEST_FRAMEWORK_HEADERS)
    return False


def _in_test_directory(path: str) -> bool:
    for part in path.split("/")[:-1]:
        if part.lower() in TEST_DIRECTORY_NAMES:
            return True
        if any(part.endswith(suffix) and part != suffix for suffix in TEST_DIRECTORY_SUFFIXES):
            return True
    return False
