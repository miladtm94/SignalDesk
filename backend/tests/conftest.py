from __future__ import annotations

import os
import tempfile

# Keep test lifecycle recovery and database writes away from real console runs.
os.environ["TRADING_CONSOLE_DATA_DIR"] = tempfile.mkdtemp(prefix="trading-console-tests-")
