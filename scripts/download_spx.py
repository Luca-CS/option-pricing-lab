"""Save a snapshot of the SPX option chain to data/raw/ (ignored by git, never published)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from optlab.marketdata.chains import download_chain  # noqa: E402
from optlab.server.settings import Settings  # noqa: E402

if __name__ == "__main__":
    print(download_chain(out_dir=Settings.load().raw))
