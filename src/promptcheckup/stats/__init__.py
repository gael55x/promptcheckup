"""Statistics: turn paired per-case differences into a verdict. Pure functions, no I/O."""

from promptcheckup.stats.gate import Decision, Verdict, decide, t_quantile

__all__ = ["Decision", "Verdict", "decide", "t_quantile"]
