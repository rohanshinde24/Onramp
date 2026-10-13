"""The bounded investigation graph (ADR-007).

    model -> tools -> model ...            (gather evidence)
    model -> validate -> END               (conclusion accepted)
    model -> validate -> model             (rejected, with the problems, while retries remain)
    model -> nudge -> model                (no tool call and no conclusion)
    any step over budget -> END            (budget exhausted; the case escalates)

There is no checkpointer: one investigation is one unit of work, rerun from the start if it
fails. Business state is never kept here.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from onramp.agent.conclusion import AgentConclusion, Observations, validate_conclusion
from onramp.agent.model import (
    ChatModel,
    Message,
    ModelRefusedError,
    ModelUnavailableError,
    ToolCall,
    ToolDefinition,
    ToolOutput,
)
from onramp.agent.prompts import NUDGE, PROMPT_VERSION, system_prompt
from onramp.agent.tools import CaseToolbox
from onramp.cases.models import CaseType

SUBMIT = "submit_conclusion"


@dataclass(frozen=True)
class Budget:
    max_model_calls: int = 12
    max_tokens: int = 60_000
    timeout_s: float = 90.0
    max_invalid_conclusions: int = 2


DEFAULT_BUDGET = Budget()


@dataclass
class InvestigationResult:
    status: str  # concluded | budget_exhausted | invalid_output | model_error | model_refused
    conclusion: AgentConclusion | None
    model_calls: int
    input_tokens: int
    output_tokens: int
    elapsed_s: float
    trajectory: list[dict[str, Any]]
    observations: Observations
    problems: list[str] = field(default_factory=list)
    model_name: str = ""
    prompt_version: str = PROMPT_VERSION


class State(TypedDict):
    transcript: list[Message]
    pending: list[ToolCall]
    model_calls: int
    input_tokens: int
    output_tokens: int
    invalid: int
    status: str | None
    conclusion: AgentConclusion | None
    problems: list[str]
    trajectory: list[dict[str, Any]]


def _tool_definitions(toolbox: CaseToolbox) -> list[ToolDefinition]:
    tools = [
        ToolDefinition(spec.name, spec.description, spec.args.model_json_schema())
        for spec in toolbox.specs
    ]
    tools.append(
        ToolDefinition(
            SUBMIT,
            "Submit your conclusion. Call exactly once, at the end.",
            AgentConclusion.model_json_schema(),
        )
    )
    return tools


def investigate(
    model: ChatModel,
    toolbox: CaseToolbox,
    case_type: CaseType,
    *,
    budget: Budget = DEFAULT_BUDGET,
    clock: Callable[[], float] = time.monotonic,
) -> InvestigationResult:
    system = system_prompt(case_type)
    tools = _tool_definitions(toolbox)
    observations = Observations()
    started = clock()

    def over_budget(state: State) -> bool:
        return (
            state["model_calls"] >= budget.max_model_calls
            or state["input_tokens"] + state["output_tokens"] >= budget.max_tokens
            or clock() - started >= budget.timeout_s
        )

    def model_node(state: State) -> dict[str, Any]:
        if over_budget(state):
            return {"status": "budget_exhausted"}
        try:
            response = model.respond(system, state["transcript"], tools)
        except ModelUnavailableError as exc:
            return {"status": "model_error", "problems": [str(exc)]}
        except ModelRefusedError as exc:
            return {"status": "model_refused", "problems": [str(exc)]}
        message = Message(
            "assistant", text=response.text, tool_calls=response.tool_calls, raw=response.raw
        )
        step = state["model_calls"] + 1
        return {
            "transcript": [*state["transcript"], message],
            "pending": list(response.tool_calls),
            "model_calls": step,
            "input_tokens": state["input_tokens"] + response.usage.input_tokens,
            "output_tokens": state["output_tokens"] + response.usage.output_tokens,
            "trajectory": [
                *state["trajectory"],
                {
                    "step": step,
                    "kind": "model",
                    "tool_calls": [c.name for c in response.tool_calls],
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
            ],
        }

    def route_after_model(state: State) -> str:
        if state["status"] is not None:
            return END
        names = {c.name for c in state["pending"]}
        if SUBMIT in names:
            return "validate"
        return "tools" if state["pending"] else "nudge"

    def tools_node(state: State) -> dict[str, Any]:
        outputs, records = [], []
        for c in state["pending"]:
            began = clock()
            result = toolbox.call(c.name, c.arguments)
            observations.record_refs |= result.refs
            observations.passage_ids |= result.passages
            body = {"error": result.error} if result.error else result.content
            outputs.append(
                ToolOutput(c.id, json.dumps(body, default=str), is_error=result.error is not None)
            )
            records.append(
                {
                    "step": state["model_calls"],
                    "kind": "tool",
                    "tool": c.name,
                    "arguments": c.arguments,
                    "refs": sorted(result.refs),
                    "passages": sorted(result.passages),
                    "error": result.error,
                    "ms": round((clock() - began) * 1000, 1),
                }
            )
        return {
            "transcript": [*state["transcript"], Message("tool", tool_outputs=tuple(outputs))],
            "pending": [],
            "trajectory": [*state["trajectory"], *records],
        }

    def validate_node(state: State) -> dict[str, Any]:
        submit = next(c for c in state["pending"] if c.name == SUBMIT)
        try:
            conclusion = AgentConclusion.model_validate(submit.arguments)
            problems = validate_conclusion(conclusion, case_type, observations)
        except ValidationError as exc:
            conclusion = None
            problems = [
                f"conclusion does not match the schema: {e['loc']} {e['msg']}"
                for e in exc.errors()[:5]
            ]
        entry = {"step": state["model_calls"], "kind": "validation", "problems": problems}
        if not problems:
            return {
                "status": "concluded",
                "conclusion": conclusion,
                "pending": [],
                "trajectory": [*state["trajectory"], entry],
            }

        invalid = state["invalid"] + 1
        outputs = tuple(
            ToolOutput(
                c.id,
                json.dumps({"rejected": problems})
                if c.name == SUBMIT
                else json.dumps({"error": "not run: submit_conclusion must be alone"}),
                is_error=True,
            )
            for c in state["pending"]
        )
        update: dict[str, Any] = {
            "invalid": invalid,
            "problems": problems,
            "pending": [],
            "transcript": [*state["transcript"], Message("tool", tool_outputs=outputs)],
            "trajectory": [*state["trajectory"], entry],
        }
        if invalid > budget.max_invalid_conclusions:
            update["status"] = "invalid_output"
        return update

    def route_after_validate(state: State) -> str:
        return END if state["status"] is not None else "model"

    def nudge_node(state: State) -> dict[str, Any]:
        return {"transcript": [*state["transcript"], Message("user", text=NUDGE)], "pending": []}

    graph = StateGraph(State)
    graph.add_node("model", model_node)
    graph.add_node("tools", tools_node)
    graph.add_node("validate", validate_node)
    graph.add_node("nudge", nudge_node)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", route_after_model, ["tools", "validate", "nudge", END])
    graph.add_edge("tools", "model")
    graph.add_edge("nudge", "model")
    graph.add_conditional_edges("validate", route_after_validate, ["model", END])
    compiled = graph.compile()

    opening = Message("user", text="Investigate this case. Start with get_case.")
    initial: State = {
        "transcript": [opening],
        "pending": [],
        "model_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "invalid": 0,
        "status": None,
        "conclusion": None,
        "problems": [],
        "trajectory": [],
    }
    # Each model call can take up to four graph steps; the budget, not LangGraph's limit,
    # is what stops an investigation.
    final = compiled.invoke(initial, {"recursion_limit": budget.max_model_calls * 4 + 10})
    return InvestigationResult(
        status=final["status"] or "budget_exhausted",
        conclusion=final["conclusion"],
        model_calls=final["model_calls"],
        input_tokens=final["input_tokens"],
        output_tokens=final["output_tokens"],
        elapsed_s=round(clock() - started, 3),
        trajectory=final["trajectory"],
        observations=observations,
        problems=final["problems"],
        model_name=model.name,
    )
