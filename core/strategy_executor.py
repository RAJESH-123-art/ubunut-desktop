"""
Strategy Executor — the self-healing execution engine.

Core idea:
  Every task has N strategies (ways to accomplish it), ordered by reliability.
  The executor tries them one by one.  After each attempt it verifies the result
  (via Verifier), records the outcome in Memory, and adapts the strategy order
  for next time.

  This produces "learning without AI":
    - Run 1: tries AT-SPI first → fails → tries CLI → succeeds
    - Run 2: tries CLI first (because it won last time) → succeeds instantly

Usage:
    from core.strategy_executor import Strategy, StrategyExecutor

    executor = StrategyExecutor("install_app")
    executor.add(Strategy("atspi",   atspi_fn,   verify_spec=app_center_open_spec()))
    executor.add(Strategy("snap_cli", snap_cli_fn, verify_spec=app_installed_spec("vlc")))
    ok = executor.run({"app_name": "vlc"})
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from core.memory import memory
from core.verifier import VerifySpec, verifier


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Strategy:
    """One specific way to accomplish a task."""
    name: str
    fn: Callable[[dict[str, Any], dict[str, Any]], object]  # bool or TaskResult
    verify_spec: VerifySpec | None = None     # what to check after running
    retry_wait: float = 2.0                   # seconds to wait before next strategy
    max_retries: int = 0                      # extra retries WITHIN this strategy (0 = try once)
    description: str = ""

    def execute_once(self, args: dict[str, Any], resources: dict[str, Any]) -> bool:
        """Run the strategy function. Returns False on exception."""
        try:
            return bool(self.fn(args, resources))
        except Exception as exc:
            logger.warning(f"Strategy '{self.name}' raised: {exc}")
            return False


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY EXECUTOR
# ─────────────────────────────────────────────────────────────────────────────
class StrategyExecutor:
    """
    Tries multiple strategies for a task, learns from outcomes.

    Memory keys (stored in core/memory.py):
        strategy:<task_name>  /  <strategy_name>:ok   → int (success count)
        strategy:<task_name>  /  <strategy_name>:err  → int (failure count)
    """

    def __init__(self, task_name: str) -> None:
        self.task_name = task_name
        self._strategies: list[Strategy] = []

    def add(self, strategy: Strategy) -> StrategyExecutor:
        """Register a strategy. Call order = default priority."""
        self._strategies.append(strategy)
        return self

    # ── History helpers ───────────────────────────────────────────────────────

    def _get_counts(self, name: str) -> tuple[float, float]:
        """Return (ok_count, err_count) from memory for a strategy."""
        ns = f"strategy:{self.task_name}"
        ok  = memory.get(ns, f"{name}:ok")  or 0
        err = memory.get(ns, f"{name}:err") or 0
        ok  = ok  if isinstance(ok,  (int, float)) else 0
        err = err if isinstance(err, (int, float)) else 0
        return float(ok), float(err)

    def _success_rate(self, name: str) -> float:
        ok, err = self._get_counts(name)
        total = ok + err
        return ok / total if total > 0 else 0.5  # 0.5 = unknown

    def _record(self, name: str, success: bool) -> None:
        ok, err = self._get_counts(name)
        ns = f"strategy:{self.task_name}"
        if success:
            memory.set(ns, f"{name}:ok",  ok  + 1)
        else:
            memory.set(ns, f"{name}:err", err + 1)

    def _ordered_strategies(self) -> list[Strategy]:
        """Return strategies sorted best-first (highest historical success rate)."""
        return sorted(
            self._strategies,
            key=lambda s: self._success_rate(s.name),
            reverse=True,
        )

    def show_stats(self) -> None:
        """Print historical success rates for all strategies."""
        logger.info(f"Strategy stats for '{self.task_name}':")
        for s in self._strategies:
            ok, err = self._get_counts(s.name)
            total = ok + err
            rate  = f"{ok/total*100:.0f}%" if total else "unknown"
            logger.info(f"  {s.name:20s}  ok={int(ok)}  err={int(err)}  rate={rate}")

    # ── Main execution ────────────────────────────────────────────────────────

    def run(self,
            args: dict[str, Any],
            resources: dict[str, Any] | None = None) -> bool:
        """
        Try each strategy (best-first based on memory) until one succeeds.

        A strategy "succeeds" when:
          • its function returns True, AND
          • the optional VerifySpec is confirmed by Verifier.

        Every outcome is recorded so next run automatically picks the better
        strategy first — learning without any AI.
        """
        if not self._strategies:
            logger.error(f"No strategies registered for '{self.task_name}'")
            return False

        resources = resources or {}
        ordered   = self._ordered_strategies()

        logger.info(
            f"StrategyExecutor '{self.task_name}': "
            f"{len(ordered)} strategies, order={[s.name for s in ordered]}"
        )

        for attempt, strategy in enumerate(ordered, 1):
            ok, err = self._get_counts(strategy.name)
            rate    = f"{ok/(ok+err)*100:.0f}%" if (ok + err) > 0 else "new"
            logger.info(
                f"  [{attempt}/{len(ordered)}] Trying '{strategy.name}' "
                f"(hist: ok={int(ok)} err={int(err)} rate={rate})"
            )

            # Run with optional retries
            result = False
            for retry in range(strategy.max_retries + 1):
                if retry > 0:
                    logger.info(f"    Retry {retry}/{strategy.max_retries} …")
                    time.sleep(1.5)
                result = strategy.execute_once(args, resources)
                if result:
                    break

            # Verify
            verified = True
            if strategy.verify_spec is not None:
                verified = verifier.verify(strategy.verify_spec)
                if not verified:
                    logger.warning(
                        f"  '{strategy.name}' ran OK but verification FAILED "
                        f"— counting as failure"
                    )

            if result and verified:
                logger.info(f"  ✅ '{strategy.name}' succeeded!")
                self._record(strategy.name, success=True)
                return True

            # Strategy failed
            logger.warning(
                f"  ❌ '{strategy.name}' failed "
                f"(result={result}, verified={verified})"
            )
            self._record(strategy.name, success=False)

            if attempt < len(ordered):
                logger.info(f"  Waiting {strategy.retry_wait}s before next strategy …")
                time.sleep(strategy.retry_wait)

        logger.error(
            f"All {len(ordered)} strategies exhausted for '{self.task_name}' — task failed"
        )
        self.show_stats()
        return False
