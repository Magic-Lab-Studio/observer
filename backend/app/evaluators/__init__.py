"""Evaluation engine for LLM Observatory."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.evaluators.judge_backends import JudgeConfig, JudgeUnavailableError, ask_judge
from app.evaluators.rubric_engine import (
    CriterionResult,
    RubricEvaluator,
    RubricLoader,
)

logger = logging.getLogger(__name__)

_DEFAULT_RUBRIC_DIR = Path(__file__).parent / "rubrics"


class EvaluatorType(str, Enum):
    """Types of evaluators."""

    LLM_JUDGE = "llm_judge"
    RULE_BASED = "rule_based"
    HUMAN = "human"


class EvaluationCriteria(BaseModel):
    """Criteria for evaluation."""

    name: str
    description: str
    weight: float = 1.0
    threshold: float | None = None


class EvaluationResult(BaseModel):
    """Result of an evaluation."""

    score: float
    criteria: dict[str, Any]
    details: dict[str, Any] | None = None
    # None: not every criterion could be evaluated, so there is no evidence to call it a pass
    passed: bool | None = True


class BaseEvaluator(ABC):
    """Base class for evaluators."""

    @abstractmethod
    async def evaluate(
        self,
        trace_id: str,
        trace_data: dict[str, Any],
        criteria: list[EvaluationCriteria],
    ) -> EvaluationResult:
        """Evaluate a trace against criteria.

        Args:
            trace_id: ID of the trace to evaluate.
            trace_data: Trace data including spans, tokens, etc.
            criteria: List of evaluation criteria.

        Returns:
            Evaluation result with score and details.
        """
        ...


class LLMJudgeEvaluator(BaseEvaluator):
    """Evaluates traces using an LLM as judge with a structured rubric.

    Scoring flow:
      1. Load YAML rubric + JSON scoring levels
      2. Compute deterministic metrics (latency, cost, error_rate, tokens)
      3. Build structured prompts for qualitative criteria (quality, safety, etc.)
      4. Call LLM to select levels for qualitative criteria
      5. Map all levels to numeric scores via the scoring scale
      6. Compute weighted composite score with auto-fail logic
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        rubric_dir: Path | None = None,
    ) -> None:
        self._judge = JudgeConfig.from_env(model=model, api_key=api_key)
        self.model = self._judge.model
        self.api_key = self._judge.api_key
        self._rubric_dir = rubric_dir or _DEFAULT_RUBRIC_DIR

        loader = RubricLoader(
            rubric_path=self._rubric_dir / "default.yaml",
            levels_path=self._rubric_dir / "scoring_levels.json",
        )
        self._rubric = loader.load()
        self._rubric_evaluator = RubricEvaluator.from_loader(loader)

    async def evaluate(
        self,
        trace_id: str,
        trace_data: dict[str, Any],
        criteria: list[EvaluationCriteria],
    ) -> EvaluationResult:
        """Score every criterion that has evidence; record the others as skipped.

        No PASS without evidence (evaluation P5): when a qualitative criterion could not be judged
        (no judge configured, the judge failed, refused or answered an unknown level) the result
        is ``evaluation_status: incomplete`` and ``passed`` is ``None``, unless the criteria that
        were scored already fail it. Skipped criteria are listed with the reason and whether
        retrying may help; their score is never filled in.
        """
        deterministic_results = self._rubric_evaluator.extract_deterministic(trace_data)
        llm_results, skipped = await self._evaluate_qualitative(trace_id, trace_data)

        all_results = deterministic_results + llm_results
        composite = self._rubric_evaluator.compute_composite(all_results)
        missing = sorted(set(composite.get("missing_required") or []))
        incomplete = bool(skipped or missing)
        failed = not composite["passed"] and bool(all_results)
        passed: bool | None = False if failed else (None if incomplete else True)

        return EvaluationResult(
            score=composite["score"],
            criteria={c["criterion"]: c["score"] for c in composite["criteria"]},
            details={
                "model": self.model,
                "judge": {"provider": self._judge.provider, "model": self.model},
                "trace_id": trace_id,
                "rubric": self._rubric.name,
                "rubric_version": self._rubric.version,
                "grade": composite["grade"],
                "has_auto_fail": composite.get("has_auto_fail", False),
                "missing_required": missing,
                "criteria_detail": composite["criteria"],
                "evaluation_status": "incomplete" if incomplete else "complete",
                "evaluated_criteria": [r.criterion_name for r in all_results],
                "skipped_criteria": skipped,
            },
            passed=passed,
        )

    async def _evaluate_qualitative(
        self,
        trace_id: str,
        trace_data: dict[str, Any],
    ) -> tuple[list[CriterionResult], list[dict[str, Any]]]:
        """Qualitative criteria judged by an LLM: ``(scored, skipped)``."""
        prompts = self._rubric_evaluator.build_llm_prompts(trace_data)
        if not prompts:
            return [], []

        config = replace(self._judge, model=self.model, api_key=self.api_key)
        results: list[CriterionResult] = []
        skipped: list[dict[str, Any]] = []

        def skip(name: str, reason: str, retryable: bool) -> None:
            skipped.append({"criterion": name, "reason": reason, "retryable": retryable})

        problem = config.problem()
        if problem:
            logger.info("LLM judge not available (%s); qualitative criteria skipped", problem)
            for prompt_data in prompts:
                skip(prompt_data["criterion_name"], problem, False)
            return results, skipped

        system_msg = (
            "You are an expert evaluator for LLM traces. "
            "You must select EXACTLY ONE level from the provided list. "
            "Return ONLY a JSON object: {\"level\": \"<selected_level>\"}"
        )
        for prompt_data in prompts:
            name = prompt_data["criterion_name"]
            level_list = ", ".join(prompt_data["levels"])
            user_msg = (
                f"{prompt_data['prompt']}\n\n"
                f"Available levels (choose exactly one): {level_list}"
            )
            try:
                content = await ask_judge(config, system_msg, user_msg)
            except JudgeUnavailableError as exc:
                logger.warning("LLM judge failed for %s: %s", name, exc.reason)
                skip(name, exc.reason, exc.retryable)
                continue
            selected = _parse_level(content)
            if selected not in prompt_data["levels"]:
                logger.warning("LLM judge answered an unknown level for %s", name)
                skip(name, "judge_invalid_level", True)
                continue
            result = self._rubric_evaluator.score_llm_result(name, selected)
            result.details["raw_response"] = content[:2000]
            results.append(result)
        return results, skipped


def _parse_level(content: str) -> str | None:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        try:
            parsed = json.loads(text[start : end + 1]) if start >= 0 < end else None
        except ValueError:
            return None
    level = parsed.get("level") if isinstance(parsed, dict) else None
    return level if isinstance(level, str) else None


class RuleBasedEvaluator(BaseEvaluator):
    """Evaluates traces based on predefined rules."""

    def __init__(self, rules: list[dict[str, Any]] | None = None) -> None:
        """Initialize the rule-based evaluator.

        Args:
            rules: List of rules to apply.
        """
        self.rules = rules or []

    async def evaluate(
        self,
        trace_id: str,
        trace_data: dict[str, Any],
        criteria: list[EvaluationCriteria],
    ) -> EvaluationResult:
        """Evaluate a trace based on rules."""
        scores = {}
        details = {}

        for criterion in criteria:
            # Apply rules based on criterion name
            if criterion.name == "max_latency":
                max_latency_ms = trace_data.get("max_latency_ms", 0)
                threshold = criterion.threshold or 5000
                scores[criterion.name] = 1.0 if max_latency_ms <= threshold else 0.0
                details[f"{criterion.name}_value"] = max_latency_ms
                details[f"{criterion.name}_threshold"] = threshold

            elif criterion.name == "max_cost":
                total_cost = trace_data.get("total_cost_usd", 0)
                threshold = criterion.threshold or 0.1
                scores[criterion.name] = 1.0 if total_cost <= threshold else 0.0
                details[f"{criterion.name}_value"] = total_cost
                details[f"{criterion.name}_threshold"] = threshold

            elif criterion.name == "error_rate":
                error_count = trace_data.get("error_count", 0)
                total_count = trace_data.get("total_spans", 1)
                error_rate = error_count / total_count if total_count > 0 else 0
                threshold = criterion.threshold or 0.1
                scores[criterion.name] = 1.0 if error_rate <= threshold else 0.0
                details[f"{criterion.name}_value"] = error_rate
                details[f"{criterion.name}_threshold"] = threshold

            else:
                # Default: pass
                scores[criterion.name] = 1.0

        avg_score = sum(scores.values()) / len(scores) if scores else 0.0

        return EvaluationResult(
            score=avg_score,
            criteria=scores,
            details=details,
            passed=avg_score >= 0.7,
        )


class HumanEvaluator(BaseEvaluator):
    """Placeholder for human evaluation (stores feedback)."""

    async def evaluate(
        self,
        trace_id: str,
        trace_data: dict[str, Any],
        criteria: list[EvaluationCriteria],
    ) -> EvaluationResult:
        """Human evaluation is not automated - returns pending status."""
        return EvaluationResult(
            score=0.0,
            criteria={c.name: 0.0 for c in criteria},
            details={"status": "pending_human_review", "trace_id": trace_id},
            passed=False,
        )


def create_evaluator(
    evaluator_type: EvaluatorType,
    **kwargs: Any,
) -> BaseEvaluator:
    """Create an evaluator instance.

    Args:
        evaluator_type: Type of evaluator to create.
        **kwargs: Additional arguments for the evaluator.
            For LLM_JUDGE: model, api_key, rubric_dir.

    Returns:
        Evaluator instance.
    """
    if evaluator_type == EvaluatorType.LLM_JUDGE:
        return LLMJudgeEvaluator(**kwargs)
    elif evaluator_type == EvaluatorType.RULE_BASED:
        return RuleBasedEvaluator(**kwargs)
    elif evaluator_type == EvaluatorType.HUMAN:
        return HumanEvaluator(**kwargs)
    else:
        raise ValueError(f"Unsupported evaluator type: {evaluator_type}")
