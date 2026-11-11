"""Configuration and logging of the structured layer."""

import logging

from _paths import SPX_CALIBRATION

from optlab.core import config
from optlab.core.log import configure, get_logger
from optlab.structured import preset, quote
from optlab.structured.context import PricingContext


def test_the_library_has_no_default_market_or_path():
    """ADR-0012: a quote, a book or a context names its market; paths are the server's Settings."""
    import pytest

    from optlab.structured.context import PricingContext

    assert SPX_CALIBRATION.exists() and not hasattr(config, "paths") and not hasattr(config, "REPO_ROOT")
    with pytest.raises(TypeError, match="needs a market"):
        quote(preset("reverse_convertible"))
    with pytest.raises(TypeError):
        PricingContext.from_calibration()  # type: ignore[call-arg]


def test_quotes_and_pricer_log_what_they_did(caplog):
    configure("DEBUG")
    try:
        with caplog.at_level(logging.DEBUG, logger="optlab"):
            quote(preset("reverse_convertible"), PricingContext.from_calibration(SPX_CALIBRATION), control=None)
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "quote bullet SPX" in messages and "replication" in messages
        assert get_logger("structured.pricer").name == "optlab.structured.pricer"
    finally:
        configure("WARNING")
        logging.getLogger("optlab").handlers[:] = [logging.NullHandler()]
