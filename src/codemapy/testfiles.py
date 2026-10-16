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
    if in_test_directory(path):
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


def in_test_directory(path: str) -> bool:
    """True if a folder in *path* (not the file name) is a test folder."""
    for part in path.split("/")[:-1]:
        if part.lower() in TEST_DIRECTORY_NAMES:
            return True
        if any(part.endswith(suffix) and part != suffix for suffix in TEST_DIRECTORY_SUFFIXES):
            return True
    return False


# Test filename -> the stem of the file it tests, with the extensions to try.
_SUBJECT_PATTERNS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"^(?P<stem>.+)_test\.go$"), (".go",)),
    (
        re.compile(r"^(?P<stem>.+)\.(?:test|spec)\.(?P<ext>[cm]?[jt]sx?|svelte)$"),
        (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".svelte"),
    ),
    (re.compile(r"^(?:test_(?P<stem>.+)|(?P<stem2>.+)_test)\.py$"), (".py",)),
    (re.compile(r"^(?P<stem>.+)_(?:spec|test)\.rb$"), (".rb",)),
    (re.compile(r"^(?P<stem>.+?)(?:Test|Tests)\.(?P<ext>java|kt|scala|cs)$"), ()),
    (re.compile(r"^(?P<stem>.+?)(?:Spec|Suite)\.(?P<ext>kt|scala)$"), ()),
    (re.compile(r"^(?:tb_(?P<stem>.+)|(?P<stem2>.+)_tb)\.s?v$"), (".v", ".sv")),
    (
        re.compile(r"^(?:test_(?P<stem>.+)|(?P<stem2>.+)_(?:test|unittest))\.(?:c|cc|cpp|cxx)$"),
        (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"),
    ),
)

# Mirrored test trees: the test's folder -> the folders its subject may sit in.
_MIRRORED_ROOTS = (
    ("src/test/", ("src/main/",)),
    ("spec/", ("app/", "lib/")),
)
_SIBLING_TEST_FOLDERS = frozenset({"__tests__", "tests", "test"})


def subject_candidates(path: str) -> list[str]:
    """Files that the test at *path* is named after, most likely first.

    The subject sits beside the test (``foo_test.go`` -> ``foo.go``), one
    level up from a ``__tests__/`` or ``tests/`` folder, or in a mirrored
    tree (``src/test/java/a/FooTest.java`` -> ``src/main/java/a/Foo.java``,
    ``spec/models/user_spec.rb`` -> ``app/models/user.rb``).
    """
    folder, _, name = path.rpartition("/")
    for pattern, extensions in _SUBJECT_PATTERNS:
        match = pattern.match(name)
        if match is None:
            continue
        stem = match.group("stem") or match.groupdict().get("stem2")
        own_ext = f".{match.group('ext')}" if "ext" in match.groupdict() and match.group("ext") else ""
        names = [f"{stem}{ext}" for ext in dict.fromkeys(((own_ext,) if own_ext else ()) + extensions)]
        return [
            f"{subject_folder}/{subject}" if subject_folder else subject
            for subject_folder in _subject_folders(folder)
            for subject in names
        ]
    return []


def _subject_folders(folder: str) -> list[str]:
    folders = [folder]
    parent, _, last = folder.rpartition("/")
    if last in _SIBLING_TEST_FOLDERS:
        folders.append(parent)
    padded = f"{folder}/"
    for test_root, subject_roots in _MIRRORED_ROOTS:
        at = padded.find(test_root)
        if at == 0 or (at > 0 and padded[at - 1] == "/"):
            folders.extend(
                (padded[:at] + subject_root + padded[at + len(test_root) :]).rstrip("/")
                for subject_root in subject_roots
            )
    return folders
