"""pandas 3.1 deprecates inplace=True, which yfinance still uses on every fetch.

The Yahoo vendor hides that one deprecation when yfinance raises it, and only
then: the same warning from anyone else's code must still be shown.
"""

import warnings

import pandas as pd
import pytest

from tradingagents.dataflows.vendors.yahoo.common import hide_yfinance_inplace_deprecations


def _inplace_calls():
    exec("frame.sort_index(inplace=True)", {"__name__": "yfinance.utils", "frame": pd.DataFrame({"a": [2, 1]})})
    pd.DataFrame({"a": [2, 1]}).sort_index(inplace=True)


@pytest.mark.unit
def test_only_yfinance_s_inplace_deprecations_are_hidden():
    # A process's filters as they stand once the Yahoo vendor is imported (pytest
    # drops filters added during collection, so they are rebuilt here): yfinance's
    # own rule re-enabling its DeprecationWarnings, then ours.
    with warnings.catch_warnings(record=True) as seen:
        warnings.resetwarnings()
        warnings.filterwarnings("default", category=DeprecationWarning, module="^yfinance")
        hide_yfinance_inplace_deprecations()
        _inplace_calls()

    inplace = [w for w in seen if "The inplace keyword" in str(w.message)]
    assert len(inplace) == 1 and inplace[0].filename == __file__


@pytest.mark.unit
def test_a_filter_added_before_yfinance_s_rule_would_lose_to_it():
    # Why the filter is installed after yfinance's import, not at package import.
    with warnings.catch_warnings(record=True) as seen:
        warnings.resetwarnings()
        hide_yfinance_inplace_deprecations()
        warnings.filterwarnings("default", category=DeprecationWarning, module="^yfinance")
        _inplace_calls()

    assert len([w for w in seen if "The inplace keyword" in str(w.message)]) == 2
