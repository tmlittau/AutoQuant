"""Seed data/transactions.csv with the initial portfolio buys.

The user contributed fixed EUR amounts on given dates; share counts are estimated
from the daily close + USD->EUR rate on each buy date (see
autoquant.portfolio.estimate_shares). Re-running is refused if a ledger already
exists, so your later transactions are never clobbered -- pass --force to rebuild.

Usage:
    .venv/bin/python scripts/seed_portfolio.py [--force] [--adapter yfinance|alphavantage]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import autoquant as aq
from autoquant import portfolio as pf

# (date, group, ticker, amount_eur) -- the starting investments.
INITIAL_BUYS = [
    ("2026-05-04", "Tech", "ARM", 200.0),
    ("2026-05-04", "Tech", "NVDA", 50.0),
    ("2026-05-10", "Tech", "AAPL", 50.0),
    ("2026-05-10", "Tech", "GOOGL", 50.0),
    ("2026-05-10", "Quantum", "IONQ", 100.0),
    ("2026-05-10", "Quantum", "RGTI", 50.0),
]


def main(force: bool = False, adapter_name: str = "alphavantage") -> None:
    path = pf.DEFAULT_LEDGER_PATH
    if path.is_file() and not force:
        existing = pf.load_transactions(path)
        if not existing.empty:
            print(f"Ledger already exists with {len(existing)} rows: {path}")
            print("Refusing to overwrite. Pass --force to rebuild from scratch.")
            return

    adapter = aq.get_adapter(adapter_name)
    print(f"Using adapter: {adapter.name}")
    ledger = pf.empty_transactions()
    for date, group, ticker, amount_eur in INITIAL_BUYS:
        ledger = pf.record_buy(
            adapter,
            date=date,
            group=group,
            ticker=ticker,
            amount_eur=amount_eur,
            transactions=ledger,
            save=False,
        )
        last = ledger.iloc[-1]
        print(
            f"{date}  {ticker:<5} {amount_eur:6.0f} EUR  ->  "
            f"{last.shares:.4f} sh @ ${last.price_usd:.2f}  (EUR/USD {last.eur_per_usd:.4f})"
        )

    pf.save_transactions(ledger, path)
    print(f"\nWrote {len(ledger)} transactions to {path}")


if __name__ == "__main__":
    name = "alphavantage"
    if "--adapter" in sys.argv:
        name = sys.argv[sys.argv.index("--adapter") + 1]
    main(force="--force" in sys.argv, adapter_name=name)
