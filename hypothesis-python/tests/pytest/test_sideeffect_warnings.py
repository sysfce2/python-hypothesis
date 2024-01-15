# This file is part of Hypothesis, which may be found at
# https://github.com/HypothesisWorks/hypothesis/
#
# Copyright the Hypothesis Authors.
# Individual contributors are listed in AUTHORS.rst and the git log.
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at https://mozilla.org/MPL/2.0/.

pytest_plugins = "pytester"

TEST_SCRIPT = """
def test_noop():
    pass
"""

LAZY_STRATEGY = "integers()"

SIDEEFFECT_STATEMENT = f"st.{LAZY_STRATEGY}.wrapped_strategy"

SIDEEFFECT_SCRIPT = f"""
from hypothesis import strategies as st

{SIDEEFFECT_STATEMENT}
"""


def test_sideeffect_warning(testdir):
    testdir.makeconftest(SIDEEFFECT_SCRIPT)
    script = testdir.makepyfile(TEST_SCRIPT)
    result = testdir.runpytest_subprocess(script)
    assert "HypothesisSideeffectWarning" in "\n".join(result.outlines)
    assert LAZY_STRATEGY in "\n".join(result.outlines)


def test_conftest_sideeffect_pinpoint_error(testdir, monkeypatch):
    # -Werror is not sufficient since warning is emitted before session start.
    monkeypatch.setenv("PYTHONWARNINGS", "error")
    testdir.makeconftest(SIDEEFFECT_SCRIPT)
    script = testdir.makepyfile(TEST_SCRIPT)
    result = testdir.runpytest_subprocess(script)
    assert "HypothesisSideeffectWarning" in "\n".join(result.errlines)
    assert SIDEEFFECT_STATEMENT in "\n".join(result.errlines)


def test_plugin_sideeffect_pinpoint_error(testdir, monkeypatch):
    # -Werror is not sufficient since warning is emitted before session start.
    monkeypatch.setenv("PYTHONWARNINGS", "error")
    # Ensure we see the correct stacktrace regardless of plugin load order
    monkeypatch.setenv("HYPOTHESIS_EXTEND_INITIALIZATION", "1")
    testdir.makepyfile(sideeffect_plugin=SIDEEFFECT_SCRIPT)
    script = testdir.makepyfile(TEST_SCRIPT)
    result = testdir.runpytest_subprocess(script, "-p", "sideeffect_plugin")
    assert "HypothesisSideeffectWarning" in "\n".join(result.errlines)
    assert SIDEEFFECT_STATEMENT in "\n".join(result.errlines)
