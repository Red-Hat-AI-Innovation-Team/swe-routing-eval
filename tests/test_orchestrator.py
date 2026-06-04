"""Tests for orchestrator.py: resume logic, budget pre-flight, sweep execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from swe_routing_eval.budget import BudgetConfig
from swe_routing_eval.cost import PriceTable, TierPricing
from swe_routing_eval.grading import GradeResult
from swe_routing_eval.ingest import SWEbenchInstance
from swe_routing_eval.orchestrator import BudgetExceeded, Orchestrator, SweepConfig
from swe_routing_eval.scaffold import AttemptResult, Scaffold
from swe_routing_eval.store import FileStore, RunRecord
from swe_routing_eval.vertex import VertexConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

OPUS_ID = "claude-opus-4-8-20251001"
SONNET_ID = "claude-sonnet-4-6-20251001"

VERTEX_CONFIG = VertexConfig(
    project_id="test-proj",
    region="us-east5",
    opus_model_id=OPUS_ID,
    sonnet_model_id=SONNET_ID,
    haiku_model_id="claude-haiku-4-5-20251001",
)

PRICE_TABLE = PriceTable(tiers={
    OPUS_ID: TierPricing(input_per_1k_tokens=0.015, output_per_1k_tokens=0.075),
    SONNET_ID: TierPricing(input_per_1k_tokens=0.003, output_per_1k_tokens=0.015),
})


def _instance(instance_id: str = "kubectl-1") -> SWEbenchInstance:
    return SWEbenchInstance(
        instance_id=instance_id,
        repo="kubernetes/kubectl",
        base_commit="abc",
        patch="",
        test_patch="",
        problem_statement="bug",
        repo_language="go",
        product="kubectl",
        fix_merge_date="2024-01-01",
        provenance="github",
        link_confidence=0.9,
        n_fail_to_pass=1,
        patch_lines=5,
        files_touched=1,
        cross_file=False,
        env_spec_hash="sha256:x",
        image_name="img:abc",
        compiled=True,
        n_runs=3,
        quarantined_tests=[],
        decontam_overlap=False,
    )


def _mock_scaffold(patch_text: str = "diff --git ...") -> MagicMock:
    scaffold = MagicMock(spec=Scaffold)
    scaffold.run.return_value = AttemptResult(
        candidate_patch=patch_text,
        seed=0,
        scaffold_version="v0.1.0",
        tokens_in=1000,
        tokens_out=200,
        turns=5,
        tool_calls=8,
        wall_clock_s=30.0,
    )
    return scaffold


def _mock_grader(resolved: bool = True) -> MagicMock:
    grader = MagicMock()
    grader.grade.return_value = GradeResult(resolved=resolved, compiled=True)
    return grader


def _workspace_factory(inst: SWEbenchInstance, idx: int) -> Path:
    return Path("/tmp/fake-workspace")


def _make_orchestrator(
    store: FileStore,
    scaffold: MagicMock | None = None,
    grader: MagicMock | None = None,
) -> Orchestrator:
    return Orchestrator(
        store=store,
        scaffold=scaffold or _mock_scaffold(),
        grader=grader or _mock_grader(),
        vertex_config=VERTEX_CONFIG,
        price_table=PRICE_TABLE,
    )


# ---------------------------------------------------------------------------
# Budget pre-flight (issue #12)
# ---------------------------------------------------------------------------


def test_project_cost_returns_per_tier_estimate(tmp_path: Path) -> None:
    orc = _make_orchestrator(FileStore(tmp_path / "runs.db"))
    cfg = SweepConfig(model_tiers=["opus", "sonnet"], k_attempts=3)
    projection = orc.project_cost(cfg, n_instances=10, avg_tokens_in=8000, avg_tokens_out=2000)
    assert "opus" in projection
    assert "sonnet" in projection
    # opus is more expensive than sonnet
    assert projection["opus"] > projection["sonnet"]


def test_dry_run_raises_budget_exceeded(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    orc = _make_orchestrator(FileStore(tmp_path / "runs.db"))
    cfg = SweepConfig(model_tiers=["opus"], k_attempts=100)
    with pytest.raises(BudgetExceeded) as exc_info:
        orc.dry_run(cfg, [_instance()] * 100, BudgetConfig(max_spend_usd=1.0))
    assert exc_info.value.projected_usd > 1.0
    assert exc_info.value.limit_usd == 1.0


def test_dry_run_prints_projection(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    orc = _make_orchestrator(FileStore(tmp_path / "runs.db"))
    cfg = SweepConfig(model_tiers=["sonnet"], k_attempts=1)
    orc.dry_run(cfg, [_instance()], BudgetConfig(max_spend_usd=100.0))
    out = capsys.readouterr().out
    assert "sonnet" in out
    assert "$" in out


# ---------------------------------------------------------------------------
# Resume: skip completed attempts (issue #10)
# ---------------------------------------------------------------------------


def test_run_skips_already_completed_attempt(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")
    scaffold = _mock_scaffold()

    # Pre-populate attempt 0 in the store
    store.save(RunRecord(
        model_id=OPUS_ID,
        instance_id="kubectl-1",
        attempt_idx=0,
        seed=0,
        scaffold_version="v0.1.0",
        candidate_patch="",
        resolved=True,
        compiled=True,
        rejected_test_edit=False,
        f2p_results=[],
        p2p_results=[],
        tokens_in=100,
        tokens_out=20,
        turns=2,
        tool_calls=3,
        wall_clock_s=5.0,
        cost_usd=0.0,
    ))

    orc = _make_orchestrator(store, scaffold=scaffold)
    cfg = SweepConfig(model_tiers=["opus"], k_attempts=1)
    orc.run(
        cfg, [_instance()], _workspace_factory,
        BudgetConfig(max_spend_usd=100.0),
    )

    # scaffold.run should NOT be called since attempt 0 is already done
    scaffold.run.assert_not_called()


def test_run_executes_pending_attempts(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")
    scaffold = _mock_scaffold()
    grader = _mock_grader(resolved=True)

    _resolved = GradeResult(resolved=True, compiled=True)
    with patch("swe_routing_eval.grading.safe_grade", return_value=_resolved):
        orc = _make_orchestrator(store, scaffold=scaffold, grader=grader)
        cfg = SweepConfig(model_tiers=["opus"], k_attempts=2, max_workers=1)
        orc.run(
            cfg, [_instance()], _workspace_factory,
            BudgetConfig(max_spend_usd=100.0),
        )

    assert scaffold.run.call_count == 2
    records = store.list_all()
    assert len(records) == 2


def test_run_saves_record_per_attempt(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")

    _resolved = GradeResult(resolved=True, compiled=True)
    with patch("swe_routing_eval.grading.safe_grade", return_value=_resolved):
        orc = _make_orchestrator(store)
        cfg = SweepConfig(model_tiers=["sonnet"], k_attempts=3, max_workers=1)
        orc.run(
            cfg, [_instance()], _workspace_factory,
            BudgetConfig(max_spend_usd=100.0),
        )

    records = store.list_all()
    assert len(records) == 3
    attempt_indices = {r.attempt_idx for r in records}
    assert attempt_indices == {0, 1, 2}


def test_run_raises_before_inference_if_budget_exceeded(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")
    scaffold = _mock_scaffold()
    orc = _make_orchestrator(store, scaffold=scaffold)
    cfg = SweepConfig(model_tiers=["opus"], k_attempts=1000)
    with pytest.raises(BudgetExceeded):
        orc.run(
            cfg, [_instance()] * 100, _workspace_factory,
            BudgetConfig(max_spend_usd=0.01),
        )
    scaffold.run.assert_not_called()
