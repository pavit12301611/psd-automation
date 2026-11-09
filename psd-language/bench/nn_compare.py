#!/usr/bin/env python3
"""Python side of the NN benchmark: identical algorithm to
bench/nn_bench.psd (2-4-1 MLP, sigmoid, per-sample SGD, 20000 epochs,
pure-Python lists — the way a Python tutorial would write it).

    python3 bench/nn_compare.py
    psd run bench/nn_bench.psd

PSD should win clearly: same source-level loops, but compiled to
native code instead of interpreted. (NumPy/PyTorch would beat both —
that is the honest boundary documented in docs/CAPABILITIES.md.)
"""
import math
import random
import time


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def main():
    rng = random.Random(7)
    sizes = [2, 4, 1]
    weights, biases = [], []
    for l in range(len(sizes) - 1):
        weights.append([[rng.random() - 0.5 for _ in range(sizes[l])]
                        for _ in range(sizes[l + 1])])
        biases.append([0.0] * sizes[l + 1])
    xs = [[0.0, 0.0], [0.0, 1.0], [1.0, 0.0], [1.0, 1.0]]
    ys = [[0.0], [1.0], [1.0], [0.0]]

    start = time.time()
    for epoch in range(20000):
        for s in range(len(xs)):
            acts = [xs[s]]
            a = xs[s]
            for l in range(len(weights)):
                a = [sigmoid(sum(weights[l][i][j] * a[j]
                                 for j in range(len(a))) + biases[l][i])
                     for i in range(len(weights[l]))]
                acts.append(a)
            deltas = [None] * len(weights)
            out = acts[-1]
            deltas[-1] = [(out[j] - ys[s][j]) * out[j] * (1 - out[j])
                          for j in range(len(out))]
            for l in range(len(weights) - 2, -1, -1):
                deltas[l] = [
                    sum(weights[l + 1][k][i] * deltas[l + 1][k]
                        for k in range(len(deltas[l + 1])))
                    * acts[l + 1][i] * (1 - acts[l + 1][i])
                    for i in range(len(weights[l + 1][0]))]
            for l in range(len(weights)):
                for i in range(len(weights[l])):
                    for j in range(len(weights[l][i])):
                        weights[l][i][j] -= 0.5 * deltas[l][i] * acts[l][j]
                    biases[l][i] -= 0.5 * deltas[l][i]

    loss = 0.0
    for s in range(len(xs)):
        a = xs[s]
        for l in range(len(weights)):
            a = [sigmoid(sum(weights[l][i][j] * a[j]
                             for j in range(len(a))) + biases[l][i])
                 for i in range(len(weights[l]))]
        loss += sum((a[j] - ys[s][j]) ** 2 for j in range(len(a)))
    print(f"python trained loss: {loss / len(xs)} ms: {(time.time() - start) * 1000:.0f}")


if __name__ == "__main__":
    main()
