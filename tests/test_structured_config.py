"""Configuration and logging of the structured layer."""

import logging

from optlab import config
from optlab.log import configure, get_logger
from optlab.structured import preset, quote
from optlab.structured.context import DEFAULT_CALIBRATION


def test_paths_default_to_the_repository_and_follow_the_environment(monkeypatch, tmp_path):
    assert DEFAULT_CALIBRATION == config.REPO_ROOT / "results" / "spx_calibration.json"
    assert DEFAULT_CALIBRATION.exists()
    monkeypatch.setenv("OPTLAB_HOME", str(tmp_path))
    p = config.paths()
    assert p.results == tmp_path / "results" and p.store == tmp_path / "data" / "store"
    monkeypatch.setenv("OPTLAB_STORE", str(tmp_path / "elsewhere"))
    assert config.paths().store == tmp_path / "elsewhere"


def test_quotes_and_pricer_log_what_they_did(caplog):
    configure("DEBUG")
    try:
        with caplog.at_level(logging.DEBUG, logger="optlab"):
            quote(preset("reverse_convertible"), control=None)
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "quote bullet SPX" in messages and "replication" in messages
        assert get_logger("structured.pricer").name == "optlab.structured.pricer"
    finally:
        configure("WARNING")
        logging.getLogger("optlab").handlers[:] = [logging.NullHandler()]
