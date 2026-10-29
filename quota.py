#!/usr/bin/env python3
"""Devin weekly quota poller + dashboard — repo-local launcher.

Equivalent to `devin-quota` after `pip install .`; kept so launchd and
`python3 quota.py serve` keep working from a checkout.
"""
from devin_quota.server import main

if __name__ == "__main__":
    main()
