"""Tests for the rubric evaluation engine."""

from pathlib import Path

import pytest

from app.evaluators.rubric_engine import (
    CriterionResult,
    RubricEvaluator,
    RubricLoader,
    TraceDataExtractor,
)

_RUBRICS_DIR = Path(__file__).resolve().parent.parent / "app" / "evaluators" / "rubrics"


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def loader() -> RubricLoader:
    ld = RubricLoader(
        rubric_path=_RUBRICS_DIR / "default.yaml",
        levels_path=_RUBRICS_DIR / "scoring_levels.json",
    )
    ld.load()
    return ld


@pytest.fixture
def evaluator(loader: RubricLoader) -> RubricEvaluator:
    return RubricEvaluator.from_loader(loader)


@pytest.fixture
def fast_trace() -> dict:
    """A fast, cheap, error-free trace."""
    return {
        "id": "trace-001",
        "name": "summarize",
        "status": "ok",
        "total_spans": 3,
        "total_cost_usd": 0.005,
        "max_latency_ms": 800,
        "error_count": 0,
        "spans": [
            {
                "id": "span-1",
                "name": "llm_call",
                "span_type": "llm",
                "tokens_input": 200,
                "tokens_output": 100,
                "input": {"messages": [{"role": "user", "content": "Summarize this"}]},
                "output": {"content": "A short summary."},
            },
            {
                "id": "span-2",
                "name": "tool_call",
                "span_type": "tool",
                "tokens_input": 0,
                "tokens_output": 0,
            },
        ],
    }


@pytest.fixture
def slow_error_trace() -> dict:
    """A slow, expensive trace with errors."""
    return {
        "id": "trace-002",
        "name": "complex_analysis",
        "status": "error",
        "total_spans": 5,
        "total_cost_usd": 0.35,
        "max_latency_ms": 12000,
        "error_count": 2,
        "spans": [
            {
                "id": "span-1",
                "name": "llm_call_1",
                "span_type": "llm",
                "tokens_input": 5000,
                "tokens_output": 3000,
                "input": {"messages": [{"role": "user", "content": "Analyze everything"}]},
                "output": {"content": ""},
            },
        ],
    }


# ── RubricLoader ─────────────────────────────────────────────────────────


class TestRubricLoader:
    def test_loads_default_rubric(self, loader: RubricLoader) -> None:
        rubric = loader.rubric
        assert rubric.name == "default"
        assert rubric.version == "1.0.0"
        assert len(rubric.criteria) == 7

    def test_loads_scoring_scales(self, loader: RubricLoader) -> None:
        scale = loader.get_scale("default_scale")
        assert len(scale.levels) == 5
        assert scale.pass_threshold == 0.6

    def test_loads_safety_scale(self, loader: RubricLoader) -> None:
        scale = loader.get_scale("safety_scale")
        assert scale.pass_threshold == 0.90
        assert scale.levels[0].name == "excellent"

    def test_missing_scale_raises(self, loader: RubricLoader) -> None:
        with pytest.raises(KeyError):
            loader.get_scale("nonexistent_scale")

    def test_criterion_categories(self, loader: RubricLoader) -> None:
        rubric = loader.rubric
        categories = {c.category for c in rubric.criteria}
        assert "performance" in categories
        assert "efficiency" in categories
        assert "reliability" in categories
        assert "quality" in categories
        assert "safety" in categories

    def test_required_criteria(self, loader: RubricLoader) -> None:
        rubric = loader.rubric
        required = [c.name for c in rubric.criteria if c.required]
        assert "latency" in required
        assert "cost" in required
        assert "error_rate" in required
        assert "output_quality" in required
        assert "safety" in required

    def test_threshold_criteria(self, loader: RubricLoader) -> None:
        rubric = loader.rubric
        threshold = [c for c in rubric.criteria if c.scoring_type == "threshold"]
        names = [c.name for c in threshold]
        assert "latency" in names
        assert "cost" in names
        assert "error_rate" in names
        assert "token_usage" in names

    def test_llm_judge_criteria(self, loader: RubricLoader) -> None:
        rubric = loader.rubric
        llm = [c for c in rubric.criteria if c.scoring_type == "llm_judge"]
        names = [c.name for c in llm]
        assert "output_quality" in names
        assert "instruction_following" in names
        assert "safety" in names

    def test_llm_criteria_have_templates(self, loader: RubricLoader) -> None:
        for c in loader.rubric.criteria:
            if c.scoring_type == "llm_judge":
                assert c.llm_prompt_template is not None
                assert "{input}" in c.llm_prompt_template or "{output}" in c.llm_prompt_template


# ── ScoringScale ─────────────────────────────────────────────────────────


class TestScoringScale:
    def test_resolve_excellent(self, loader: RubricLoader) -> None:
        scale = loader.get_scale("default_scale")
        level = scale.resolve_level(0.95)
        assert level.name == "excellent"
        assert level.score == 1.0

    def test_resolve_good(self, loader: RubricLoader) -> None:
        scale = loader.get_scale("default_scale")
        level = scale.resolve_level(0.80)
        assert level.name == "good"
        assert level.score == 0.8

    def test_resolve_acceptable(self, loader: RubricLoader) -> None:
        scale = loader.get_scale("default_scale")
        level = scale.resolve_level(0.55)
        assert level.name == "acceptable"
        assert level.score == 0.6

    def test_resolve_poor(self, loader: RubricLoader) -> None:
        scale = loader.get_scale("default_scale")
        level = scale.resolve_level(0.35)
        assert level.name == "poor"
        assert level.score == 0.3

    def test_resolve_failing(self, loader: RubricLoader) -> None:
        scale = loader.get_scale("default_scale")
        level = scale.resolve_level(0.10)
        assert level.name == "failing"
        assert level.score == 0.0
        assert level.auto_fail is True

    def test_resolve_clamps_high(self, loader: RubricLoader) -> None:
        scale = loader.get_scale("default_scale")
        level = scale.resolve_level(1.5)
        assert level.name == "excellent"

    def test_resolve_clamps_low(self, loader: RubricLoader) -> None:
        scale = loader.get_scale("default_scale")
        level = scale.resolve_level(-0.5)
        assert level.name == "failing"

    def test_safety_scale_stricter(self, loader: RubricLoader) -> None:
        scale = loader.get_scale("safety_scale")
        # 0.80 would be "good" on default_scale but "acceptable" on safety_scale
        level = scale.resolve_level(0.80)
        assert level.name == "acceptable"


# ── TraceDataExtractor ──────────────────────────────────────────────────


class TestTraceDataExtractor:
    def test_extract_simple_field(self, evaluator: RubricEvaluator) -> None:
        ext = TraceDataExtractor()
        from app.evaluators.rubric_engine import CriterionExtraction

        extraction = CriterionExtraction(extract_field="max_latency_ms")
        value = ext.extract_value({"max_latency_ms": 1500}, extraction)
        assert value == 1500

    def test_extract_nested_field(self, evaluator: RubricEvaluator) -> None:
        ext = TraceDataExtractor()
        from app.evaluators.rubric_engine import CriterionExtraction

        extraction = CriterionExtraction(extract_field="spans.0.name")
        value = ext.extract_value({"spans": [{"name": "llm_call"}]}, extraction)
        assert value == "llm_call"

    def test_compute_error_rate(self, evaluator: RubricEvaluator) -> None:
        ext = TraceDataExtractor()
        from app.evaluators.rubric_engine import CriterionExtraction

        extraction = CriterionExtraction(
            compute="error_count / total_spans",
            fields=["error_count", "total_spans"],
        )
        value = ext.extract_value({"error_count": 2, "total_spans": 10}, extraction)
        assert value == pytest.approx(0.2)

    def test_compute_error_rate_zero_spans(self, evaluator: RubricEvaluator) -> None:
        ext = TraceDataExtractor()
        from app.evaluators.rubric_engine import CriterionExtraction

        extraction = CriterionExtraction(
            compute="error_count / total_spans",
            fields=["error_count", "total_spans"],
        )
        value = ext.extract_value({"error_count": 0, "total_spans": 0}, extraction)
        assert value == 0.0

    def test_compute_token_usage(self, evaluator: RubricEvaluator) -> None:
        ext = TraceDataExtractor()
        from app.evaluators.rubric_engine import CriterionExtraction

        extraction = CriterionExtraction(
            compute="sum(tokens_input) + sum(tokens_output)",
            fields=["spans"],
        )
        trace = {
            "spans": [
                {"tokens_input": 100, "tokens_output": 50},
                {"tokens_input": 200, "tokens_output": 100},
            ]
        }
        value = ext.extract_value(trace, extraction)
        assert value == 450.0

    def test_extract_llm_context_filters_type(self, evaluator: RubricEvaluator) -> None:
        ext = TraceDataExtractor()
        from app.evaluators.rubric_engine import CriterionExtraction

        extraction = CriterionExtraction(
            spans=True,
            include_fields=["input", "output"],
            filter_type="llm",
        )
        trace = {
            "spans": [
                {"span_type": "llm", "input": "q", "output": "a"},
                {"span_type": "tool", "input": "x", "output": "y"},
            ]
        }
        contexts = ext.extract_llm_context(trace, extraction)
        assert len(contexts) == 1
        assert contexts[0]["input"] == "q"

    def test_extract_llm_context_no_filter(self, evaluator: RubricEvaluator) -> None:
        ext = TraceDataExtractor()
        from app.evaluators.rubric_engine import CriterionExtraction

        extraction = CriterionExtraction(
            spans=True,
            include_fields=["output"],
            filter_type="",
        )
        trace = {
            "spans": [
                {"span_type": "llm", "output": "hello"},
                {"span_type": "tool", "output": "world"},
            ]
        }
        contexts = ext.extract_llm_context(trace, extraction)
        assert len(contexts) == 2


# ── RubricEvaluator: Deterministic Scoring ──────────────────────────────


class TestDeterministicScoring:
    def test_fast_trace_latency(self, evaluator: RubricEvaluator, fast_trace: dict) -> None:
        results = evaluator.extract_deterministic(fast_trace)
        latency = [r for r in results if r.criterion_name == "latency"][0]
        assert latency.level == "excellent"  # 800ms < 1000ms
        assert latency.level_score == 1.0

    def test_fast_trace_cost(self, evaluator: RubricEvaluator, fast_trace: dict) -> None:
        results = evaluator.extract_deterministic(fast_trace)
        cost = [r for r in results if r.criterion_name == "cost"][0]
        assert cost.level == "excellent"  # $0.005 < $0.01
        assert cost.level_score == 1.0

    def test_fast_trace_error_rate(self, evaluator: RubricEvaluator, fast_trace: dict) -> None:
        results = evaluator.extract_deterministic(fast_trace)
        err = [r for r in results if r.criterion_name == "error_rate"][0]
        assert err.level == "excellent"  # 0 errors
        assert err.level_score == 1.0

    def test_slow_trace_latency(self, evaluator: RubricEvaluator, slow_error_trace: dict) -> None:
        results = evaluator.extract_deterministic(slow_error_trace)
        latency = [r for r in results if r.criterion_name == "latency"][0]
        assert latency.level == "failing"  # 12000ms > 10000ms
        assert latency.level_score == 0.0

    def test_slow_trace_cost(self, evaluator: RubricEvaluator, slow_error_trace: dict) -> None:
        results = evaluator.extract_deterministic(slow_error_trace)
        cost = [r for r in results if r.criterion_name == "cost"][0]
        assert cost.level == "failing"  # $0.35 > $0.25
        assert cost.level_score == 0.0

    def test_slow_trace_error_rate(
        self, evaluator: RubricEvaluator, slow_error_trace: dict
    ) -> None:
        results = evaluator.extract_deterministic(slow_error_trace)
        err = [r for r in results if r.criterion_name == "error_rate"][0]
        assert err.level == "failing"  # 2/5 = 0.4 > 0.25
        assert err.level_score == 0.0

    def test_token_usage(self, evaluator: RubricEvaluator, fast_trace: dict) -> None:
        results = evaluator.extract_deterministic(fast_trace)
        tokens = [r for r in results if r.criterion_name == "token_usage"][0]
        # 200+100+0+0 = 300 → excellent (< 1000)
        assert tokens.level == "excellent"

    def test_missing_data_yields_default(self, evaluator: RubricEvaluator) -> None:
        results = evaluator.extract_deterministic({"id": "empty"})
        # latency and cost: field not found → None → failing
        latency = [r for r in results if r.criterion_name == "latency"][0]
        cost = [r for r in results if r.criterion_name == "cost"][0]
        assert latency.level == "failing"
        assert latency.raw_value is None
        assert cost.level == "failing"
        assert cost.raw_value is None
        # error_rate and token_usage: compute returns 0.0 from empty dict → excellent
        err = [r for r in results if r.criterion_name == "error_rate"][0]
        assert err.level == "excellent"

    def test_boundary_values(self, evaluator: RubricEvaluator) -> None:
        # Exactly at boundary: 1000ms ≤ 1000 → excellent
        trace = {
            "max_latency_ms": 1000,
            "total_cost_usd": 0.01,
            "error_count": 0,
            "total_spans": 1,
            "spans": [],
        }
        results = evaluator.extract_deterministic(trace)
        latency = [r for r in results if r.criterion_name == "latency"][0]
        assert latency.level == "excellent"

    def test_boundary_just_over(self, evaluator: RubricEvaluator) -> None:
        trace = {
            "max_latency_ms": 1001,
            "total_cost_usd": 0.0,
            "error_count": 0,
            "total_spans": 1,
            "spans": [],
        }
        results = evaluator.extract_deterministic(trace)
        latency = [r for r in results if r.criterion_name == "latency"][0]
        assert latency.level == "good"


# ── RubricEvaluator: LLM Prompt Building ────────────────────────────────


class TestLLMPromptBuilding:
    def test_builds_prompts_for_qualitative_criteria(
        self, evaluator: RubricEvaluator, fast_trace: dict
    ) -> None:
        prompts = evaluator.build_llm_prompts(fast_trace)
        names = [p["criterion_name"] for p in prompts]
        assert "output_quality" in names
        assert "safety" in names

    def test_prompt_contains_trace_data(self, evaluator: RubricEvaluator, fast_trace: dict) -> None:
        prompts = evaluator.build_llm_prompts(fast_trace)
        for p in prompts:
            assert "Summarize this" in p["prompt"] or "A short summary" in p["prompt"]

    def test_prompt_has_available_levels(
        self, evaluator: RubricEvaluator, fast_trace: dict
    ) -> None:
        prompts = evaluator.build_llm_prompts(fast_trace)
        for p in prompts:
            assert "excellent" in p["prompt"]
            assert "failing" in p["prompt"]

    def test_no_prompts_when_no_llm_spans(self, evaluator: RubricEvaluator) -> None:
        trace = {"spans": [{"span_type": "tool", "output": "data"}]}
        prompts = evaluator.build_llm_prompts(trace)
        assert len(prompts) == 0


# ── RubricEvaluator: Score LLM Result ──────────────────────────────────


class TestScoreLLMResult:
    def test_score_excellent(self, evaluator: RubricEvaluator) -> None:
        result = evaluator.score_llm_result("output_quality", "excellent")
        assert result.level == "excellent"
        assert result.level_score == 1.0
        assert result.auto_fail is False

    def test_score_failing(self, evaluator: RubricEvaluator) -> None:
        result = evaluator.score_llm_result("safety", "failing")
        assert result.level == "failing"
        assert result.level_score == 0.0
        assert result.auto_fail is True

    def test_invalid_criterion_raises(self, evaluator: RubricEvaluator) -> None:
        with pytest.raises(KeyError):
            evaluator.score_llm_result("nonexistent", "good")


# ── RubricEvaluator: Composite Scoring ─────────────────────────────────


class TestCompositeScoring:
    def test_all_excellent(self, evaluator: RubricEvaluator) -> None:
        results = [
            CriterionResult("latency", "Latency", "performance", "excellent", 1.0, 1.0),
            CriterionResult("cost", "Cost", "efficiency", "excellent", 1.0, 1.0),
            CriterionResult("error_rate", "Error Rate", "reliability", "excellent", 1.0, 1.5),
            CriterionResult("output_quality", "Output Quality", "quality", "excellent", 1.0, 2.0),
            CriterionResult("safety", "Safety", "safety", "excellent", 1.0, 2.5),
        ]
        composite = evaluator.compute_composite(results)
        assert composite["score"] == 1.0
        assert composite["grade"] == "A"
        assert composite["passed"] is True
        assert composite["has_auto_fail"] is False

    def test_mixed_scores(self, evaluator: RubricEvaluator) -> None:
        results = [
            CriterionResult("latency", "Latency", "performance", "good", 0.8, 1.0),
            CriterionResult("cost", "Cost", "efficiency", "excellent", 1.0, 1.0),
            CriterionResult("error_rate", "Error Rate", "reliability", "acceptable", 0.6, 1.5),
            CriterionResult("output_quality", "Output Quality", "quality", "good", 0.8, 2.0),
            CriterionResult("safety", "Safety", "safety", "excellent", 1.0, 2.5),
        ]
        composite = evaluator.compute_composite(results)
        # weighted: (0.8*1 + 1.0*1 + 0.6*1.5 + 0.8*2 + 1.0*2.5) / (1+1+1.5+2+2.5)
        # = (0.8 + 1.0 + 0.9 + 1.6 + 2.5) / 8.0 = 6.8 / 8.0 = 0.85
        assert composite["score"] == pytest.approx(0.85, abs=0.01)
        assert composite["grade"] == "B"
        assert composite["passed"] is True

    def test_auto_fail_blocks_pass(self, evaluator: RubricEvaluator) -> None:
        results = [
            CriterionResult("latency", "Latency", "performance", "excellent", 1.0, 1.0),
            CriterionResult("cost", "Cost", "efficiency", "excellent", 1.0, 1.0),
            CriterionResult("safety", "Safety", "safety", "failing", 0.0, 2.5, auto_fail=True),
        ]
        composite = evaluator.compute_composite(results)
        # Even though score might be > 0.6, auto_fail blocks passing
        assert composite["has_auto_fail"] is True
        assert composite["passed"] is False

    def test_empty_results(self, evaluator: RubricEvaluator) -> None:
        composite = evaluator.compute_composite([])
        assert composite["score"] == 0.0
        assert composite["grade"] == "F"
        assert composite["passed"] is False

    def test_grade_boundaries(self, evaluator: RubricEvaluator) -> None:
        test_cases = [
            (0.95, "A"),
            (0.75, "B"),
            (0.55, "C"),
            (0.25, "D"),
            (0.05, "F"),
        ]
        for score, expected_grade in test_cases:
            results = [
                CriterionResult("latency", "Latency", "performance", "excellent", score, 1.0),
            ]
            composite = evaluator.compute_composite(results)
            assert composite["grade"] == expected_grade, (
                f"Score {score} should be grade {expected_grade}"
            )

    def test_weight_affects_composite(self, evaluator: RubricEvaluator) -> None:
        # Heavy weight on failing criterion
        results = [
            CriterionResult("latency", "Latency", "performance", "excellent", 1.0, 0.1),
            CriterionResult("safety", "Safety", "safety", "failing", 0.0, 10.0),
        ]
        composite = evaluator.compute_composite(results)
        # (1.0*0.1 + 0.0*10.0) / (0.1+10.0) ≈ 0.0099
        assert composite["score"] == pytest.approx(0.0099, abs=0.01)
        assert composite["grade"] == "F"


# ── LLMJudgeEvaluator Integration (mocked) ────────────────────────────


class TestLLMJudgeEvaluatorMocked:
    """Tests for LLMJudgeEvaluator with mocked LLM API calls."""

    def _make_evaluator(self) -> "LLMJudgeEvaluator":  # noqa: F821
        from app.evaluators import LLMJudgeEvaluator

        return LLMJudgeEvaluator(
            model="gpt-4",
            api_key="test-key-123",
            rubric_dir=_RUBRICS_DIR,
        )

    def _make_trace_data(self) -> dict:
        return {
            "id": "trace-test",
            "name": "test_fn",
            "status": "ok",
            "total_spans": 1,
            "total_cost_usd": 0.005,
            "max_latency_ms": 500,
            "error_count": 0,
            "spans": [
                {
                    "id": "s1",
                    "name": "llm_call",
                    "span_type": "llm",
                    "tokens_input": 100,
                    "tokens_output": 50,
                    "input": {"messages": [{"role": "user", "content": "Hello"}]},
                    "output": {"content": "Hi there!"},
                },
            ],
        }

    @pytest.mark.anyio
    async def test_deterministic_only_when_no_api_key(self):
        """Without API key, only deterministic criteria are scored."""
        evaluator = self._make_evaluator()
        evaluator.api_key = None

        from app.evaluators import EvaluationCriteria

        result = await evaluator.evaluate(
            trace_id="t1",
            trace_data=self._make_trace_data(),
            criteria=[EvaluationCriteria(name="latency", description="Latency")],
        )
        # Deterministic criteria should still produce scores
        assert "latency" in result.criteria
        assert "cost" in result.criteria
        assert "error_rate" in result.criteria
        # LLM criteria should be missing (no API key)
        assert "output_quality" not in result.criteria
        assert "safety" not in result.criteria

    @pytest.mark.anyio
    async def test_evaluate_returns_composite_score(self):
        """Full evaluate returns proper composite structure."""
        evaluator = self._make_evaluator()
        evaluator.api_key = None  # deterministic only


        result = await evaluator.evaluate(
            trace_id="t1",
            trace_data=self._make_trace_data(),
            criteria=[],
        )
        assert 0.0 <= result.score <= 1.0
        assert result.details is not None
        assert "grade" in result.details
        assert "rubric" in result.details
        assert result.details["rubric"] == "default"

    @pytest.mark.anyio
    async def test_composite_with_all_deterministic_passing(self):
        """Fast trace should produce passing composite from deterministic alone."""
        evaluator = self._make_evaluator()
        evaluator.api_key = None


        result = await evaluator.evaluate(
            trace_id="t1",
            trace_data=self._make_trace_data(),
            criteria=[],
        )
        # latency=excellent, cost=excellent, error_rate=excellent, token_usage=excellent
        # All 4 deterministic pass → composite should be high
        assert result.score >= 0.9
        assert result.details["grade"] == "A"
        # ...but the qualitative criteria were never judged: no PASS without evidence (P5)
        assert result.passed is None
        assert result.details["evaluation_status"] == "incomplete"
        assert {s["reason"] for s in result.details["skipped_criteria"]} == {"judge_not_configured"}
