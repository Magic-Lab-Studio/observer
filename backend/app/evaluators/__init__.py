"""Evaluation engine for LLM Observatory."""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

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
    passed: bool = True


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
        self.model = model or os.getenv("OBSERVATORY_JUDGE_MODEL", "gpt-4o")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
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
        # Step 1: deterministic scoring (no LLM needed)
        deterministic_results = self._rubric_evaluator.extract_deterministic(trace_data)

        # Step 2: LLM-judged qualitative scoring
        llm_results = await self._evaluate_qualitative(trace_id, trace_data)

        # Step 3: merge and compute composite
        all_results = deterministic_results + llm_results
        composite = self._rubric_evaluator.compute_composite(all_results)

        return EvaluationResult(
            score=composite["score"],
            criteria={c["criterion"]: c["score"] for c in composite["criteria"]},
            details={
                "model": self.model,
                "trace_id": trace_id,
                "rubric": self._rubric.name,
                "rubric_version": self._rubric.version,
                "grade": composite["grade"],
                "has_auto_fail": composite["has_auto_fail"],
                "missing_required": composite["missing_required"],
                "criteria_detail": composite["criteria"],
            },
            passed=composite["passed"],
        )

    async def _evaluate_qualitative(
        self,
        trace_id: str,
        trace_data: dict[str, Any],
    ) -> list[CriterionResult]:
        """Evaluate qualitative criteria via LLM, returning structured level selections."""
        prompts = self._rubric_evaluator.build_llm_prompts(trace_data)

        if not prompts:
            return []

        if not self.api_key:
            logger.warning("No API key configured, skipping LLM qualitative evaluation")
            return []

        results: list[CriterionResult] = []
        try:
            import httpx

            for prompt_data in prompts:
                level_list = ", ".join(prompt_data["levels"])
                system_msg = (
                    "You are an expert evaluator for LLM traces. "
                    "You must select EXACTLY ONE level from the provided list. "
                    "Return ONLY a JSON object: {\"level\": \"<selected_level>\"}"
                )
                user_msg = (
                    f"{prompt_data['prompt']}\n\n"
                    f"Available levels (choose exactly one): {level_list}"
                )

                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self.model,
                            "messages": [
                                {"role": "system", "content": system_msg},
                                {"role": "user", "content": user_msg},
                            ],
                            "temperature": 0.0,
                            "response_format": {"type": "json_object"},
                        },
                    )
                    response.raise_for_status()
                    body = response.json()

                content = body["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                selected_level = parsed.get("level", prompt_data["levels"][-1])

                if selected_level not in prompt_data["levels"]:
                    logger.warning(
                        "LLM returned invalid level '%s' for criterion '%s', using default",
                        selected_level,
                        prompt_data["criterion_name"],
                    )
                    selected_level = prompt_data["levels"][-1]

                result = self._rubric_evaluator.score_llm_result(
                    prompt_data["criterion_name"],
                    selected_level,
                )
                result.details["raw_response"] = content
                results.append(result)

        except ImportError:
            logger.warning("httpx not available, skipping LLM evaluation")
        except Exception as e:
            logger.warning("LLM evaluation failed (%s), skipping qualitative criteria", e)

        return results


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
