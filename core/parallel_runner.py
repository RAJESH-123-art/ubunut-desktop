"""
ParallelRunner — Executes a TaskDAG using a thread pool.

Algorithm:
  1. Find all nodes with no pending deps → submit to pool
  2. As each completes → publish result → unlock dependents
  3. Handle retries for failed nodes
  4. Repeat until DAG is complete or timeout
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
    as_completed,
)
from concurrent.futures import (
    TimeoutError as FuturesTimeoutError,
)
from typing import Any, Callable, Dict, List

from loguru import logger

from core.action_policy import requires_approval
from core.replanner import replanner
from core.result_bus import result_bus
from core.task_contract import TaskResult, normalize_task_result
from core.task_dag import TaskDAG, TaskNode

# Type for task executor function
ExecutorFn = Callable[[Dict[str, Any], Dict[str, Any]], object]


class ParallelRunner:
    def __init__(
        self,
        max_workers: int = 4,
        timeout: float = 300.0,
        executor_builder: Callable[[str, Dict], tuple[ExecutorFn, Dict, Dict]] | None = None,
    ) -> None:
        self.max_workers = max_workers
        self.timeout = timeout
        self.executor_builder = executor_builder  # Function to build executor from intent
        self._dag: TaskDAG | None = None
        self._futures: Dict[str, Future] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()

    def run(self, dag: TaskDAG) -> Dict[str, int]:
        """Execute the DAG and return summary."""
        dag.validate()
        self._dag = dag
        self._futures.clear()
        self._stop_event.clear()
        self._pause_event.clear()
        result_bus.clear()
        for node in dag.nodes.values():
            if node.status == "done" and node.result is not None:
                result_bus.publish(node.id, TaskResult(ok=True, data=node.result))

        t0 = time.time()
        logger.info(f"ParallelRunner: Starting DAG with {len(dag.nodes)} nodes, max_workers={self.max_workers}")

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while not dag.is_complete() and not self._stop_event.is_set():
                # Check timeout
                if time.time() - t0 > self.timeout:
                    logger.error(f"ParallelRunner: Timeout reached ({self.timeout}s)")
                    self.cancel()
                    break

                # Always collect completed work while paused. Pausing blocks
                # only new submissions; it never strands finished futures.
                self._process_completed()
                if dag.propagate_blocked():
                    logger.warning("ParallelRunner: blocked nodes whose dependencies cannot succeed")

                if self._pause_event.is_set():
                    time.sleep(0.05)
                    continue

                # Submit all ready nodes
                ready_nodes = dag.ready()
                for node in ready_nodes:
                    if node.id not in self._futures:
                        self._submit_node(pool, node)

                # Small sleep to prevent busy waiting
                time.sleep(0.05)

            if self._stop_event.is_set():
                self._cancel_pending_nodes()
            # Python calls that are already running cannot be killed safely;
            # allow them to finish, then record their real outcomes.
            self._wait_for_all()
            self._process_completed()

        summary = dag.summary()
        logger.info(f"ParallelRunner: Completed — {summary}")
        return summary

    def _submit_node(self, pool: ThreadPoolExecutor, node: TaskNode) -> None:
        """Submit a single node for execution."""
        dag = self._dag
        if not dag:
            return

        dag.mark_running(node.id)
        logger.info(f"[ParallelRunner] Submitting: {node.id} (intent={node.intent}, deps={node.deps})")

        # Build executor for this intent
        if self.executor_builder:
            try:
                executor_fn, args, resources = self.executor_builder(node.intent, node.args)
            except Exception as exc:
                logger.error(f"[ParallelRunner] Failed to build executor for {node.intent}: {exc}")
                dag.mark_failed(node.id, error=f"Executor build failed: {exc}")
                result_bus.publish(node.id, TaskResult(ok=False, error=str(exc)))
                return
        else:
            # Fallback: direct import (for testing)
            executor_fn = lambda a, r: self._default_execute(node.intent, a, r)
            args = node.args
            resources = {}

        fut = pool.submit(self._run_node, node, executor_fn, args, resources)
        with self._lock:
            self._futures[node.id] = fut

    def _run_node(
        self,
        node: TaskNode,
        executor_fn: ExecutorFn,
        args: Dict[str, Any],
        resources: Dict[str, Any]
    ) -> TaskResult:
        """Execute one task node inside a thread."""
        try:
            # Inject dependency results into args if placeholders exist
            final_args = self._resolve_dependencies(args, node.deps)
            for internal_key in ("_recovery_for", "_recovery_next"):
                final_args.pop(internal_key, None)
            task_resources = {
                **resources,
                "dag_node_id": node.id,
                "result_bus": result_bus,
            }

            logger.debug(f"[ParallelRunner] Executing {node.id} with args: {final_args}")
            raw_result = executor_fn(final_args, task_resources)

            # Legacy boolean executors may publish their payload to the result bus
            # and return only success/failure. Keep that data-flow contract while
            # allowing unified TaskResult/ActionOutcome returns to pass through.
            if isinstance(raw_result, bool):
                if raw_result:
                    published = result_bus.get(node.id)
                    if published is not None:
                        result = normalize_task_result(published)
                        if result.data is None:
                            result = self._copy_result_with_data(
                                result, {"status": "completed"}
                            )
                        return result
                    result = normalize_task_result(raw_result)
                    result.data = {"status": "completed"}
                    return result

                # A legacy False carried no dispatch information and historically
                # was safe to retry/replan. Mark it not-dispatched explicitly so
                # conservative unified-contract defaults do not change that behavior.
                result = normalize_task_result(
                    raw_result, dispatch_status="not_dispatched"
                )
                result.error = "Task returned False"
                return result

            return normalize_task_result(raw_result)
        except Exception as exc:
            logger.error(f"[ParallelRunner] Node {node.id} failed: {exc}")
            return TaskResult(ok=False, error=str(exc))

    @staticmethod
    def _copy_result_with_data(result: TaskResult, data: Any) -> TaskResult:
        """Copy a unified result while replacing only its dependency payload."""
        return TaskResult(
            state=result.state,
            data=data,
            evidence=result.evidence,
            confidence=result.confidence,
            uncertainty=result.uncertainty,
            dispatch_status=result.dispatch_status,
            side_effect=result.side_effect,
            warnings=result.warnings,
            error=result.error,
            metadata=result.metadata,
            actions=result.actions,
        )

    @staticmethod
    def _must_not_repeat(node: TaskNode, result: TaskResult) -> bool:
        """Return whether retry/replanning could duplicate an uncertain effect."""
        if result.state == "uncertain":
            return True
        if result.dispatch_status not in ("dispatched", "unknown"):
            return False
        return (
            result.side_effect in ("external", "destructive")
            or requires_approval(node.intent, node.args)
        )

    def _resolve_dependencies(self, args: Dict[str, Any], deps: List[str]) -> Dict[str, Any]:
        """Resolve scalar and nested placeholders such as ``${node.urls[0]}``."""
        import copy
        import re

        pattern = re.compile(r"\$\{([^.}]+)\.([^}]+)\}")

        def lookup(value: Any, path: str) -> Any:
            current = value
            for match in re.finditer(r"([^.\[\]]+)|\[(\d+)\]", path):
                mapping_key, list_index = match.groups()
                if list_index is not None and isinstance(current, list):
                    current = current[int(list_index)]
                elif mapping_key is not None and isinstance(current, dict):
                    current = current[mapping_key]
                else:
                    raise KeyError(path)
            return current

        def replace(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            if not isinstance(value, str):
                return value

            exact = pattern.fullmatch(value)
            if exact and exact.group(1) in deps:
                dep_result = result_bus.get(exact.group(1))
                if dep_result is not None:
                    try:
                        return copy.deepcopy(lookup(dep_result.data, exact.group(2)))
                    except (KeyError, IndexError, TypeError):
                        return value

            def substitute(match: re.Match[str]) -> str:
                dep_id, path = match.groups()
                if dep_id not in deps:
                    return match.group(0)
                dep_result = result_bus.get(dep_id)
                if dep_result is None:
                    return match.group(0)
                try:
                    return str(lookup(dep_result.data, path))
                except (KeyError, IndexError, TypeError):
                    return match.group(0)

            return pattern.sub(substitute, value)

        return replace(args)

    def _default_execute(self, intent: str, args: Dict, resources: Dict) -> bool:
        """Default executor - imports and runs task directly."""
        try:
            # This is a fallback - normally executor_builder is provided
            logger.warning(f"No executor_builder provided for {intent}")
            return False
        except Exception:
            return False

    def _process_completed(self) -> None:
        """Check completed futures and update DAG."""
        dag = self._dag
        if not dag:
            return

        done_futures = []
        with self._lock:
            for node_id, fut in list(self._futures.items()):
                if fut.done():
                    done_futures.append((node_id, fut))

        for node_id, fut in done_futures:
            with self._lock:
                self._futures.pop(node_id, None)
            
            try:
                result: TaskResult = fut.result()
                node = dag.get(node_id)
                recovery_for = str(node.args.get("_recovery_for", "")) if node else ""
                if result.ok:
                    dag.mark_done(node_id, result=result.data)
                    result_bus.publish(node_id, result)
                    if recovery_for:
                        dag.mark_done(recovery_for, result=result.data)
                        result_bus.publish(recovery_for, result)
                        self._skip_remaining_recovery_nodes(recovery_for, except_node=node_id)
                        logger.info(f"[ParallelRunner] ✅ {node_id} recovered {recovery_for}")
                    else:
                        logger.info(f"[ParallelRunner] ✅ {node_id} done")
                else:
                    logger.warning(f"[ParallelRunner] ❌ {node_id} failed: {result.error}")
                    # Never automatically repeat an uncertain outcome or a
                    # possibly-dispatched consequential effect.
                    if node and self._must_not_repeat(node, result):
                        logger.warning(
                            f"[ParallelRunner] Refusing retry/replan for {node_id}: "
                            f"state={result.state}, side_effect={result.side_effect}, "
                            f"dispatch_status={result.dispatch_status}"
                        )
                        dag.mark_failed(node_id, error=result.error or "")
                        result_bus.publish(node_id, result)
                        if recovery_for:
                            dag.mark_failed(
                                recovery_for,
                                error=result.error or "Recovery outcome is unsafe to repeat",
                            )
                            result_bus.publish(recovery_for, result)
                            self._skip_remaining_recovery_nodes(
                                recovery_for,
                                except_node=node_id,
                                reason="A recovery outcome is unsafe to repeat",
                            )
                    # Check if retry allowed
                    elif node and node.can_retry():
                        logger.info(f"[ParallelRunner] Retrying {node_id} (attempt {node.retry_count + 1}/{node.max_retries})")
                        if dag.retry_node(node_id):
                            # Will be picked up in next loop iteration
                            pass
                        else:
                            dag.mark_failed(node_id, error=result.error or "")
                            result_bus.publish(node_id, result)
                    else:
                        if recovery_for and node is not None:
                            self._advance_recovery(node, result)
                        else:
                            recovered = False
                            if node is not None:
                                try:
                                    dag.mark_recovering(node_id, error=result.error or "")
                                    recovered = replanner.on_failure(node, dag)
                                    if recovered:
                                        logger.info(f"[ParallelRunner] Replanner injected alternatives for {node_id}")
                                except Exception as exc:
                                    logger.error(f"[ParallelRunner] Replanner failed for {node_id}: {exc}")
                            if not recovered:
                                dag.mark_failed(node_id, error=result.error or "")
                                result_bus.publish(node_id, result)
            except Exception as exc:
                logger.error(f"[ParallelRunner] Future {node_id} raised: {exc}")
                dag.mark_failed(node_id, error=str(exc))
                result_bus.publish(node_id, TaskResult(ok=False, error=str(exc)))

    def _advance_recovery(self, node: TaskNode, result: TaskResult) -> None:
        """Activate the next fallback, or fail the original node when exhausted."""
        dag = self._dag
        if dag is None:
            return
        recovery_for = str(node.args.get("_recovery_for", ""))
        next_id = str(node.args.get("_recovery_next", ""))
        if next_id:
            dag.set_status(node.id, "skipped", error=result.error or "")
            dag.set_status(next_id, "pending")
            logger.info(f"[ParallelRunner] Trying next recovery alternative: {next_id}")
            return
        dag.mark_failed(node.id, error=result.error or "")
        dag.mark_failed(recovery_for, error=f"All recovery alternatives failed; last error: {result.error}")
        result_bus.publish(recovery_for, result)

    def _skip_remaining_recovery_nodes(
        self,
        recovery_for: str,
        except_node: str,
        reason: str = "A previous recovery alternative succeeded",
    ) -> None:
        dag = self._dag
        if dag is None:
            return
        for candidate in list(dag.nodes.values()):
            if candidate.id != except_node and candidate.args.get("_recovery_for") == recovery_for and candidate.status in ("pending", "deferred"):
                dag.set_status(candidate.id, "skipped", error=reason)

    def _wait_for_all(self) -> None:
        """Wait for all submitted futures to complete."""
        with self._lock:
            futures = list(self._futures.values())
        
        try:
            for fut in as_completed(futures, timeout=30):
                try:
                    fut.result()
                except Exception as exc:
                    logger.debug(f"ParallelRunner: future failed during shutdown: {exc}")
        except FuturesTimeoutError:
            logger.warning("ParallelRunner: some running tasks did not finish within the 30s shutdown wait")

    def _cancel_pending_nodes(self) -> None:
        """Mark work that never started as cancelled; keep running futures tracked."""
        dag = self._dag
        if dag is None:
            return
        for node in list(dag.nodes.values()):
            if node.status == "pending":
                dag.mark_failed(node.id, error="cancelled")
        with self._lock:
            for node_id, fut in self._futures.items():
                if fut.cancel():
                    dag.mark_failed(node_id, error="cancelled")

    def pause(self) -> None:
        """Pause scheduling new DAG nodes; currently running nodes may finish."""
        self._pause_event.set()

    def resume(self) -> None:
        """Resume scheduling after :meth:`pause`."""
        self._pause_event.clear()

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    def cancel(self) -> None:
        """Cancel pending work and stop scheduling new nodes."""
        self._stop_event.set()

    def stop(self) -> None:
        """Backward-compatible alias for :meth:`cancel`."""
        self.cancel()
