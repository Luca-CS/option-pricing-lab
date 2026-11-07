"""The registry of instruments: one declarative table, so that loads, calendars and the term-sheet form follow it.

Only `enabled` names are loaded and offered. The others are declared with their tickers and switched on by changing
one word. The list is chosen today, so history built on it has a survivorship bias: these names all survived to 2026,
and the bias grows the further back a replay starts. Pure data (no pandas): its table for the lake is
`marketdata.ingest.as_rows`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    id: str
    name: str
    asset_class: str  # index, equity or fx
    currency: str
    place: str  # calendar of its fixings
    yahoo: str
    vol_index: str = ""  # implied volatility index (30 days), empty when none is free
    total_return: str = ""  # Yahoo ticker of the total-return index, gives the realised dividend yield
    enabled: bool = True
    group: str = ""  # whose defaults of the hedging costs it takes (structured/defaults.py)


UNIVERSE = (
    Instrument("SPX", "S&P 500", "index", "USD", "NYSE", "^GSPC", "VIX", "^SP500TR", group="index_us"),
    Instrument("NDX", "Nasdaq 100", "index", "USD", "NYSE", "^NDX", "VXN", group="index_us"),
    Instrument("RUT", "Russell 2000", "index", "USD", "NYSE", "^RUT", "RVX", "^RUTTR", group="index_us"),
    Instrument("SX5E", "Euro Stoxx 50", "index", "EUR", "EUREX", "^STOXX50E", "V2TX", group="index_eu"),
    Instrument("AAPL", "Apple", "equity", "USD", "NYSE", "AAPL", "VXAPL", group="equity_us"),
    Instrument("AMZN", "Amazon", "equity", "USD", "NYSE", "AMZN", "VXAZN", group="equity_us"),
    Instrument("GOOGL", "Alphabet", "equity", "USD", "NYSE", "GOOGL", "VXGOG", group="equity_us"),
    Instrument("GS", "Goldman Sachs", "equity", "USD", "NYSE", "GS", "VXGS", group="equity_us"),
    Instrument("IBM", "IBM", "equity", "USD", "NYSE", "IBM", "VXIBM", group="equity_us"),
    # Declared, not loaded yet (scope agreed on 5 November 2026: names with a free implied volatility first).
    Instrument("DAX", "DAX", "index", "EUR", "XETRA", "^GDAXI", enabled=False, group="index_eu"),
    Instrument("CAC", "CAC 40", "index", "EUR", "EURONEXT", "^FCHI", enabled=False, group="index_eu"),
    Instrument("FTSE", "FTSE 100", "index", "GBP", "LSE", "^FTSE", enabled=False),
    Instrument("NKY", "Nikkei 225", "index", "JPY", "TSE", "^N225", enabled=False),
    Instrument("MSFT", "Microsoft", "equity", "USD", "NYSE", "MSFT", enabled=False, group="equity_us"),
    Instrument("NVDA", "Nvidia", "equity", "USD", "NYSE", "NVDA", enabled=False, group="equity_us"),
    Instrument("JPM", "JPMorgan Chase", "equity", "USD", "NYSE", "JPM", enabled=False, group="equity_us"),
    Instrument("ASML", "ASML", "equity", "EUR", "EURONEXT", "ASML.AS", enabled=False),
    Instrument("LVMH", "LVMH", "equity", "EUR", "EURONEXT", "MC.PA", enabled=False),
    Instrument("SAP", "SAP", "equity", "EUR", "XETRA", "SAP.DE", enabled=False),
    # Pairs: base then quote, the rate is quote per base. Loaded from the ECB and FRED (sources/fx.py), not from their Yahoo tickers.
    Instrument("EURUSD", "Euro / US dollar", "fx", "USD", "FX", "EURUSD=X"),
    Instrument("USDJPY", "US dollar / yen", "fx", "JPY", "FX", "USDJPY=X"),
    Instrument("GBPUSD", "Sterling / US dollar", "fx", "USD", "FX", "GBPUSD=X"),
)
_BY_ID = {i.id: i for i in UNIVERSE}
REFERENCE_INSTRUMENT = {"NYSE": "SPX", "EUREX": "SX5E"}  # whose traded days define a place's observed calendar
PLACE_CALENDAR = {"NYSE": "nyse", "EUREX": "eurex"}  # the rule-based calendar of a place, for term sheets


def instrument(name: str) -> Instrument:
    try:
        return _BY_ID[name]
    except KeyError:
        raise KeyError(f"unknown instrument {name!r}, known: {', '.join(_BY_ID)}") from None


def enabled(asset_class: str | None = None) -> tuple:
    return tuple(i for i in UNIVERSE if i.enabled and (asset_class is None or i.asset_class == asset_class))


def enabled_underlyings() -> tuple:
    """The enabled instruments with prices and a surface: indices and shares, not the FX pairs."""
    return tuple(i for i in enabled() if i.asset_class != "fx")


def enabled_names() -> tuple:
    """Names offered by the term-sheet form."""
    return tuple(i.id for i in enabled_underlyings())


def pair_currencies(pair: str) -> tuple[str, str]:
    """(base, quote) of a pair: its rate is quote per base, so domestic per foreign with foreign = base."""
    if len(pair) != 6:
        raise ValueError(f"{pair!r} is not a currency pair")
    return pair[:3], pair[3:]
