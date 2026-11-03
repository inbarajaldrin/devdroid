"""Run the existing search CLI against an immutable FTS snapshot, without refresh.

The task forbids writes under CQ_CACHE_DIR. Normal search creates/migrates the
database and refreshes it, so this smoke harness substitutes just those two
boundaries while exercising main(), argument parsing and the actual search.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cq


if __name__ == "__main__":
    sys.argv[0] = str(Path(cq.__file__).resolve())
    uri = cq.FTS_DB.resolve().as_uri() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        with patch.object(cq, "create_db", return_value=connection), patch.object(
            cq, "refresh_index", return_value=0
        ):
            cq.main()
