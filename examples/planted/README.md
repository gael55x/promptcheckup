# Planted example: why paired replay matters

A trading-like agent where each day's market move hits every prompt equally. Prompt B has a small real edge (+0.3 per day) over prompt A.

- **Naive comparison:** prompt A's week (a rising market) against prompt B's week (a falling market). This mostly measures the market, so it picks A.
- **Paired replay:** both prompts on the same days. The market move cancels, and the gate finds B's edge.

No LLM or API key needed. From the repository root:

```bash
PYTHONPATH=src python examples/planted/planted.py
```

```
Naive comparison (different weeks): B - A = -1.56  -> picks A
Paired replay (same days): mean B - A = +0.36, 95% CI [+0.17, +0.55] -> better
```

The same behaviour is checked by `tests/examples/test_planted.py`.
