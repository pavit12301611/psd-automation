#!/usr/bin/env python3
"""Trainer that exits nonzero: used by tests/cases/ml_trainer_fail.psd."""
import sys

print("boom", file=sys.stderr)
sys.exit(1)
