#!/usr/bin/env python3
"""Stdlib-only OLS trainer used by examples/ml_interop.psd.

This is the seam where a real ML runtime plugs in: PSD writes a json data
file, runs this script with `run(["python3", ...])`, and reads the resulting
json model file. Swap this script for scikit-learn / PyTorch / R code and
the PSD side does not change.

Usage: python3 train_ols.py <data.json> <model.json>
  data.json:  [[x, y], ...]
  model.json: {"w": ..., "b": ..., "note": "..."}
"""
import json
import sys


def main() -> int:
    data_path, model_path = sys.argv[1], sys.argv[2]
    with open(data_path) as fh:
        data = json.load(fh)
    n = len(data)
    mx = sum(p[0] for p in data) / n
    my = sum(p[1] for p in data) / n
    sxx = sum((p[0] - mx) ** 2 for p in data)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in data)
    if sxx == 0:
        print("zero variance in x", file=sys.stderr)
        return 1
    w = sxy / sxx
    b = my - w * mx
    with open(model_path, "w") as fh:
        json.dump({"w": w, "b": b,
                   "note": "trained by python stdlib OLS (swap for any ML runtime)"},
                  fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
