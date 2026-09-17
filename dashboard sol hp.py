#!/usr/bin/env python3
"""Chop & Absorption State Machine LP Terminal — Solana & Robinhood Mobile Edition.
100% Parameter & Logic Parity with Telegram Bot (bot_sol_lp.py).
Multi-Chain: 🟠 Solana + 🟢 Robinhood via GMGN Open API.
Akses Browser HP: http://<IP_VPS>:8771
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import web

if __name__ == "__main__":
    web.run_server()
