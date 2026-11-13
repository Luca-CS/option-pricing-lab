"""Update the historical data lake from the public sources and print the quality report (`optlab lake update`).

    optlab lake update [--only cboe,stoxx,fred,treasury,ecb,mof,boj,boe,fx,yahoo,universe] [--lake PATH]
    optlab lake update --yahoo-chains | --import-sample ZIP | --dolt FOLDER

Data goes to the settings' lake (`OPTLAB_LAKE`, by default `<data>/lake`): never into the repository. The Yahoo option
chains are a manual step, during US hours only (Yahoo blanks bid and ask otherwise).
"""

from __future__ import annotations

from pathlib import Path

from optlab.marketdata.ingest import load_yahoo_chains, update_all
from optlab.marketdata.lake import Lake
from optlab.marketdata.quality import report_text, run_report
from optlab.marketdata.sources.archives import import_historicaldata
from optlab.marketdata.sources.dolthub import Dolt, load_dolthub
from optlab.server.settings import Settings
from optlab.server.tools import say


def main(
    only: list[str] | None = None,
    lake: str | Path | None = None,
    yahoo_chains: bool = False,
    import_sample: str | None = None,
    dolt: str | None = None,
    log=say,
) -> None:
    settings = Settings.load()
    store = Lake(lake or settings.lake)
    log(f"lake: {store.root}")
    if only is not None or not (yahoo_chains or import_sample or dolt):
        update_all(store, only, log=log)
    if yahoo_chains:
        for r in load_yahoo_chains(store):
            log(r)
    if import_sample:
        for r in import_historicaldata(store, import_sample):
            log(r)
    if dolt:
        for r in load_dolthub(store, Dolt.from_environment(settings.data, dolt)):
            log(r)
    log(report_text(store, run_report(store)))
