from __future__ import annotations

from core.capability_registry import Capability, CapabilityContract, CapabilityRegistry, StructuredCapabilities
from core.task_contract import ActionOutcome, TaskResult
from core.task_runtime import RuntimeAction, RuntimeDecision, TaskRuntime


def test_capability_contract_dispatches_through_task_runtime() -> None:
    registry = CapabilityRegistry()
    state = {"saved": False}
    registry.register(Capability(
        CapabilityContract(
            name="file.save",
            description="Save a document through a reusable adapter",
            side_effect="local_write",
            inputs=("path",),
            observable_outcomes=("file exists",),
            recovery_hints=("check destination",),
        ),
        execute=lambda args, _runtime: (
            state.__setitem__("saved", args["path"]) or TaskResult(
                True, evidence=[{"path": args["path"]}], dispatch_status="confirmed", side_effect="local_write"
            )
        ),
    ))

    runtime = TaskRuntime.with_capabilities(
        capabilities=registry,
        observe=lambda _runtime: {"saved": state["saved"]},
        decide=lambda _runtime, observed: (
            RuntimeDecision(complete=True)
            if observed["saved"]
            else RuntimeDecision(action=RuntimeAction("file.save", {"path": "/tmp/report.txt"}))
        ),
        verify=lambda _action, outcome, _before, after, _runtime: (
            outcome.success and after["saved"] == "/tmp/report.txt"
        ),
        final_verify=lambda _runtime, observed: observed["saved"] == "/tmp/report.txt",
    )

    result = runtime.run("save report")

    assert result.success
    assert registry.contracts()[0].name == "file.save"
    action_event = next(event for event in result.state.events if event.phase == "action")
    assert action_event.outcome["metadata"]["capability"] == "file.save"


def test_unknown_capability_fails_without_dispatch() -> None:
    outcome = CapabilityRegistry().execute(
        RuntimeAction("missing"),
        TaskRuntime(
            observe=lambda _runtime: {},
            decide=lambda _runtime, _observation: RuntimeDecision(complete=True),
            act=lambda *_args: TaskResult(True),
            verify=lambda *_args: True,
        ).run("placeholder").state,
    )

    assert outcome.state == "failed"
    assert outcome.dispatch_status == "not_attempted"


def test_structured_capabilities_reuse_one_executor_and_preserve_expectation() -> None:
    class Executor:
        def __init__(self) -> None:
            self.steps = []

        def execute_step(self, step, **kwargs):
            self.steps.append((step, kwargs))
            return ActionOutcome(
                state="succeeded",
                data={"action": step.action},
                evidence=[{"action": step.action}],
                dispatch_status="confirmed",
                side_effect="read",
            )

        def close(self):
            pass

    executor = Executor()
    registry = CapabilityRegistry()
    StructuredCapabilities(executor=executor, goal="open a page").install(registry)
    runtime = TaskRuntime.with_capabilities(
        capabilities=registry,
        observe=lambda _runtime: {"done": bool(executor.steps)},
        decide=lambda _runtime, observed: (
            RuntimeDecision(complete=True)
            if observed["done"]
            else RuntimeDecision(action=RuntimeAction(
                "structured.navigate", {"url": "https://example.test", "expect": {"url_contains": "example.test"}}
            ))
        ),
        verify=lambda _action, outcome, _before, _after, _runtime: outcome.success,
        final_verify=lambda _runtime, observed: observed["done"],
    )

    assert runtime.run("open a page").success
    step, kwargs = executor.steps[0]
    assert step.action == "navigate"
    assert step.expect == {"url_contains": "example.test"}
    assert kwargs["goal"] == "open a page"


def test_discovery_filters_capabilities_to_observed_interfaces() -> None:
    registry = CapabilityRegistry()
    registry.register(Capability(
        CapabilityContract("browser.click", "Click DOM", interfaces=("browser_dom",)),
        execute=lambda *_args: TaskResult(True),
    ))
    registry.register(Capability(
        CapabilityContract("desktop.click", "Click accessibility", interfaces=("atspi",)),
        execute=lambda *_args: TaskResult(True),
    ))
    registry.register(Capability(
        CapabilityContract("local.transform", "Transform verified data"),
        execute=lambda *_args: TaskResult(True),
    ))

    names = [item.name for item in registry.discover({"atspi"})]

    assert names == ["desktop.click", "local.transform"]


def test_dispatch_refuses_an_unavailable_observed_interface() -> None:
    invoked = False
    registry = CapabilityRegistry()

    def execute(*_args):
        nonlocal invoked
        invoked = True
        return TaskResult(True)

    registry.register(Capability(
        CapabilityContract("desktop.click", "Click accessibility", interfaces=("atspi",)),
        execute=execute,
    ))

    runtime = TaskRuntime(
        observe=lambda _runtime: {"interfaces": ["browser_dom"]},
        decide=lambda _runtime, _observation: RuntimeDecision(
            action=RuntimeAction("desktop.click")
        ),
        act=lambda action, state: registry.execute(action, state),
        verify=lambda *_args: False,
    )
    result = runtime.run("click unavailable desktop control")

    assert not invoked
    assert result.state.status == "blocked"
    action = next(
        event for event in result.state.events
        if event.phase == "action" and event.outcome
    )
    assert action.outcome["dispatch_status"] == "not_attempted"
    assert action.outcome["metadata"]["missing_interfaces"] == ["atspi"]
