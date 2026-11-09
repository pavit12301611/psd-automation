#!/usr/bin/env python3
"""Trainer that hangs: used by tests/cases/ml_timeout.psd to prove
try_capture timeouts kill the process (code == -1). Never run directly."""
import sys
import time

time.sleep(30)
sys.exit(0)
