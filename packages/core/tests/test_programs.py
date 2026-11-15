"""An external program is found in the absolute folders of the PATH only, never in the current folder (security.md,
finding 44): a `curl` planted where the process runs is not the one that runs."""

import os
import sys

import pytest

from optlab.core import programs


@pytest.fixture
def folders(tmp_path, monkeypatch):
    system, cwd = tmp_path / "system", tmp_path / "work"
    system.mkdir()
    cwd.mkdir()
    name = "tool.exe" if sys.platform == "win32" else "tool"
    for folder in (system, cwd):
        exe = folder / name
        exe.write_text("")
        exe.chmod(0o755)
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("PATHEXT", ".EXE")
    return system, cwd, name


def test_a_program_is_found_in_an_absolute_folder_of_the_path(folders):
    system, cwd, name = folders
    assert programs.find("tool", path=os.pathsep.join([".", "work", "", str(system)])) == str(system / name)


def test_the_current_folder_is_never_searched(folders):
    system, cwd, name = folders
    assert programs.find("tool", path=os.pathsep.join([".", "", "work"])) is None
    with pytest.raises(FileNotFoundError, match="no-such-program"):
        programs.require("no-such-program-anywhere")
