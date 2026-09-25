"""One-off read-only probe of the real AKShare 1.18.97 interfaces used by fofoca-data.

Usage:
    .venv/bin/python scripts/probe_akshare.py

Prints column names, dtypes, head rows and sizes for:
- fund_name_em()                 (name/type directory)
- fund_open_fund_daily_em()      (purchase/redemption status + recent dated NAV snapshot)
- fund_open_fund_info_em(symbol, indicator=...) for a known open fund

Also verifies the eligibility selector runs end-to-end against the live
feeds and reports the resulting candidate counts (including whether the
spec-mandated example ``166009`` dual-open LOF is selected).

This script performs no writes anywhere.
"""

from __future__ import annotations

import sys
import traceback

import akshare as ak


def show(label, fn, *args, **kwargs):
    print(f"\n===== {label} =====")
    try:
        df = fn(*args, **kwargs)
        print("type:", type(df).__name__)
        print("shape:", getattr(df, "shape", None))
        print("columns:", list(getattr(df, "columns", [])))
        print("dtypes:")
        print(df.dtypes)
        print("head(3):")
        print(df.head(3).to_string())
        return df
    except Exception:
        traceback.print_exc()
        return None


def main() -> int:
    name_df = show("fund_name_em", ak.fund_name_em)
    daily_df = show("fund_open_fund_daily_em", ak.fund_open_fund_daily_em)

    code = "000001"
    if name_df is not None and "基金代码" in name_df.columns:
        codes = name_df["基金代码"].astype(str).tolist()
        for wanted in ("000001", "110022", "050025"):
            if wanted in codes:
                code = wanted
                break
    print(f"\nprobe history code: {code}")
    show(
        f'fund_open_fund_info_em({code}, "单位净值走势")',
        ak.fund_open_fund_info_em,
        symbol=code,
        indicator="单位净值走势",
    )
    show(
        f'fund_open_fund_info_em({code}, "累计净值走势")',
        ak.fund_open_fund_info_em,
        symbol=code,
        indicator="累计净值走势",
    )

    # Verify the eligibility selector end-to-end against the live feeds.
    print("\n===== eligibility selector (live) =====")
    try:
        from fofoca_data.eligibility import select_candidates
        from fofoca_data.provider import AkshareProvider

        provider = AkshareProvider(request_delay_seconds=0.0)
        result = select_candidates(provider, None)
        for k, v in result.counts.as_dict().items():
            print(f"  {k}: {v}")
        print(f"  166009 in candidates: {'166009' in {c.code for c in result.candidates}}")
        print(
            f"  snapshot_dates: {[d.isoformat() for d in result.daily_snapshot.dates]}"
        )
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
