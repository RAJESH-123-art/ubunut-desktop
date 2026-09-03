"""
ParallelRunner — Executes a TaskDAG using a thread pool.

Algorithm:
  1. Find all nodes with no pending deps → submit to pool
  2. As each completes → publish result → unlock dependents
  3. Handle retries for failed nodes
  4. Repeat until DAG is complete or timeout
"""
from __future__ import annotations
import time
import threading
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from typing import Dict, List, Optional, Callable, Any
from loguru import logger

from core.task_dag import TaskDAG, TaskNode
from core.result_bus import result_bus, TaskResult
from core.replanner import replanner


# Type for task executor function
ExecutorFn = Callable[[Dict[str, Any], Dict[str, Any]], bool]


class ParallelRunner:
    def __init__(
        self,
        max_workers: int = 4,
        timeout: float = 300.0,
        executor_builder: Optional[Callable[[str, Dict], tuple[ExecutorFn, Dict, Dict]]] = None,
    ) -> None:
        self.max_workers = max_workers
        self.timeout = timeout
        self.executor_builder = executor_builder  # Function to build executor from intent
        self._dag: Optional[TaskDAG] = None
        self._futures: Dict[str, Future] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def run(self, dag: TaskDAG) -> Dict[str, int]:
        """Execute the DAG and return summary."""
        self._dag = dag
        self._futures.clear()
        self._stop_event.clear()
        result_bus.clear()
        
        t0 = time.time()
        logger.info(f"ParallelRunner: Starting DAG with {len(dag.nodes)} nodes, max_workers={self.max_workers}")

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while not dag.is_complete() and not self._stop_event.is_set():
                # Check timeout
                if time.time() - t0 > self.timeout:
                    logger.error(f"ParallelRunner: Timeout reached ({self.timeout}s)")
                    self._cancel_all()
                    break

                # Submit all ready nodes
                ready_nodes = dag.ready()
                for node in ready_nodes:
                    if node.id not in self._futures:
                        self._submit_node(pool, node)

                # Process completed futures
                self._process_completed()

                # Small sleep to prevent busy waiting
                time.sleep(0.05)

            # Wait for any remaining futures
            self._wait_for_all()

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
            
            logger.debug(f"[ParallelRunner] Executing {node.id} with args: {final_args}")
            ok = executor_fn(final_args, resources)
            
            if ok:
                # Get result from task (if it published one)
                result_data = result_bus.get(node.id)
                if result_data and result_data.data is not None:
                    result = TaskResult(ok=True, data=result_data.data)
                else:
                    result = TaskResult(ok=True, data={"status": "completed"})
            else:
                result = TaskResult(ok=False, error="Task returned False")
            
            return result
        except Exception as exc:
            logger.error(f"[ParallelRunner] Node {node.id} failed: {exc}")
            return TaskResult(ok=False, error=str(exc))

    def _resolve_dependencies(self, args: Dict[str, Any], deps: List[str]) -> Dict[str, Any]:
        """Resolve ${dep_id.key} placeholders in args with dependency results."""
        import re
        resolved = args.copy()
        
        for dep_id in deps:
            dep_result = result_bus.get(dep_id)
            if not dep_result or not dep_result.data:
                continue
            
            data = dep_result.data
            if not isinstance(data, dict):
                continue
                
            # Replace placeholders like ${search_web_0.urls[0]} or ${install_app_0.package}
            for key, value in data.items():
                placeholder = f"${{{dep_id}.{key}}}"
                if isinstance(value, (str, int, float, bool)):
                    # Replace in all string args
                    for arg_key, arg_val in resolved.items():
                        if isinstance(arg_val, str) and placeholder in arg_val:
                            resolved[arg_key] = arg_val.replace(placeholder, str(value))
        
        return resolved

    def _default_execute(self, intent: str, args: Dict, resources: Dict) -> bool:
        """Default executor - imports and runs task directly."""
        try:
            from tasks import __all__ as task_modules
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
                if result.ok:
                    dag.mark_done(node_id, result=result.data)
                    logger.info(f"[ParallelRunner] ✅ {node_id} done")
                else:
                    logger.warning(f"[ParallelRunner] ❌ {node_id} failed: {result.error}")
                    # Check if retry allowed
                    node = dag.get(node_id)
                    if node and node.can_retry():
                        logger.info(f"[ParallelRunner] Retrying {node_id} (attempt {node.retry_count + 1}/{node.max_retries})")
                        if dag.retry_node(node_id):
                            # Will be picked up in next loop iteration
                            pass
                        else:
                            dag.mark_failed(node_id, error=result.error)
                            result_bus.publish(node_id, result)
                    else:
                        dag.mark_failed(node_id, error=result.error)
                        result_bus.publish(node_id, result)
                        # Self-healing: ask the Replanner for alternative strategies
                        # for this intent. If it injects new nodes, they'll be
                        # picked up as "ready" on the next loop iteration.
                        if node is not None:
                            try:
                                if replanner.on_failure(node, dag):
                                    logger.info(f"[ParallelRunner] Replanner injected alternatives for {node_id}")
                            except Exception as exc:
                                logger.error(f"[ParallelRunner] Replanner failed for {node_id}: {exc}")
            except Exception as exc:
                logger.error(f"[ParallelRunner] Future {node_id} raised: {exc}")
                dag.mark_failed(node_id, error=str(exc))
                result_bus.publish(node_id, TaskResult(ok=False, error=str(exc)))

    def _wait_for_all(self) -> None:
        """Wait for all submitted futures to complete."""
        with self._lock:
            futures = list(self._futures.values())
        
        for fut in as_completed(futures, timeout=30):
            try:
                fut.result()
            except Exception:
                pass

    def _cancel_all(self) -> None:
        """Cancel all pending futures."""
        self._stop_event.set()
        with self._lock:
            for fut in self._futures.values():
                fut.cancel()
            self._futures.clear()

    def stop(self) -> None:
        """Stop the runner gracefully."""
        self._stop_event.set()