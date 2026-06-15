"""Eval orchestrator: schedule model×instance×attempt matrix (issues #10, #12).

Resumable: completed (model_id, instance_id, attempt_idx) triples are skipped
by checking the run store before scheduling work.

Bounded concurrency via ThreadPoolExecutor; the grade binary and Vertex API
calls are the bottleneck, not Python threads.

Budget pre-flight (issue #12): projects total spend before any inference runs;
refuses to start if the projection exceeds BudgetConfig.max_spend_usd.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from swe_routing_eval.budget import BudgetConfig, dry_run_estimate
from swe_routing_eval.cost import PriceTable
from swe_routing_eval.grading import Grader, GraderError, safe_grade
from swe_routing_eval.ingest import SWEbenchInstance
from swe_routing_eval.scaffold import AttemptResult, CLIScaffold, Scaffold
from swe_routing_eval.store import RunRecord, Store
from swe_routing_eval.vertex import VERTEX_TIERS, VertexConfig

logger = logging.getLogger(__name__)

WorkspaceFactory = Callable[[SWEbenchInstance, int, str], Path]


@dataclass
class SweepConfig:
    model_tiers: list[str]
    k_attempts: int
    max_workers: int = 4
    base_seed: int = 0


class BudgetExceeded(Exception):
    def __init__(self, projected_usd: float, limit_usd: float) -> None:
        self.projected_usd = projected_usd
        self.limit_usd = limit_usd
        super().__init__(
            f"Projected sweep cost ${projected_usd:.2f} exceeds "
            f"limit ${limit_usd:.2f}. "
            "Use --max-spend-usd to raise the cap or --dry-run to inspect."
        )


class GraderCircuitBreaker(Exception):
    """Raised when consecutive grader errors indicate a systemic failure."""

    def __init__(self, last_error: str, consecutive: int) -> None:
        self.last_error = last_error
        self.consecutive = consecutive
        super().__init__(
            f"Grader circuit breaker tripped after {consecutive} consecutive "
            f"grader errors. Last error: {last_error}"
        )


class Orchestrator:
    """Schedules and runs the model×instance×attempt matrix.

    Usage::

        orc = Orchestrator(store, scaffold, grader, vertex_config, price_table)
        orc.dry_run(sweep_config, instances, budget)   # prints projection, no inference
        orc.run(sweep_config, instances, workspace_factory, budget)
    """

    def __init__(
        self,
        store: Store,
        scaffold: Scaffold,
        grader: Grader,
        vertex_config: VertexConfig,
        price_table: PriceTable,
        cli_scaffold: CLIScaffold | None = None,
    ) -> None:
        self._store = store
        self._scaffold = scaffold
        self._grader = grader
        self._vertex_config = vertex_config
        self._price_table = price_table
        self._cli_scaffold = cli_scaffold

    # ------------------------------------------------------------------
    # Budget pre-flight (issue #12)
    # ------------------------------------------------------------------

    def project_cost(
        self,
        sweep_config: SweepConfig,
        n_instances: int,
        avg_tokens_in: int = 450_000,
        avg_tokens_out: int = 7_000,
    ) -> dict[str, float]:
        """Return projected total cost per tier without running any inference."""
        projection: dict[str, float] = {}
        for tier in sweep_config.model_tiers:
            model_id = self._vertex_config.model_id(tier)
            pricing = self._price_table._lookup(model_id)
            projection[tier] = dry_run_estimate(
                n_instances=n_instances,
                k_attempts=sweep_config.k_attempts,
                avg_tokens_in=avg_tokens_in,
                avg_tokens_out=avg_tokens_out,
                price_in_per_1k=pricing.input_per_1k_tokens,
                price_out_per_1k=pricing.output_per_1k_tokens,
            )
        return projection

    def dry_run(
        self,
        sweep_config: SweepConfig,
        instances: list[SWEbenchInstance],
        budget: BudgetConfig,
        avg_tokens_in: int = 450_000,
        avg_tokens_out: int = 7_000,
    ) -> None:
        """Print cost projection and check against the budget cap.

        Raises BudgetExceeded if total projected cost > budget.max_spend_usd.
        """
        projection = self.project_cost(
            sweep_config, len(instances), avg_tokens_in, avg_tokens_out
        )
        total = sum(projection.values())
        n = len(instances)
        k = sweep_config.k_attempts
        print(f"Dry-run cost projection ({n} instances, k={k}):")
        for tier, cost in projection.items():
            print(f"  {tier:8s}: ${cost:.2f}")
        print(f"  {'TOTAL':8s}: ${total:.2f}  (cap: ${budget.max_spend_usd:.2f})")
        if total > budget.max_spend_usd:
            raise BudgetExceeded(projected_usd=total, limit_usd=budget.max_spend_usd)

    # ------------------------------------------------------------------
    # Sweep execution (issue #10)
    # ------------------------------------------------------------------

    def run(
        self,
        sweep_config: SweepConfig,
        instances: list[SWEbenchInstance],
        workspace_factory: WorkspaceFactory,
        budget: BudgetConfig,
        avg_tokens_in: int = 450_000,
        avg_tokens_out: int = 7_000,
        workspace_cleanup: Callable[[Path], None] | None = None,
        grader_circuit_limit: int = 3,
    ) -> None:
        """Run the full model×instance×attempt sweep.

        Skips already-completed triples (resume support).
        Warns when running spend reaches budget.warn_at_fraction of the cap.
        Raises GraderCircuitBreaker after *grader_circuit_limit* consecutive
        grader errors (default 3), indicating a systemic grader failure.
        """
        self.dry_run(sweep_config, instances, budget, avg_tokens_in, avg_tokens_out)

        work = [
            (tier, inst, attempt_idx)
            for inst in instances
            for tier in sweep_config.model_tiers
            for attempt_idx in range(sweep_config.k_attempts)
        ]
        pending = [
            (tier, inst, idx)
            for tier, inst, idx in work
            if not self._is_done(self._vertex_config.model_id(tier), inst.instance_id, idx)
        ]
        skipped = len(work) - len(pending)
        if skipped:
            logger.info(
                "Resuming: %d/%d complete, %d pending", skipped, len(work), len(pending)
            )

        spent_usd = 0.0
        warn_threshold = budget.max_spend_usd * budget.warn_at_fraction

        with ThreadPoolExecutor(max_workers=sweep_config.max_workers) as pool:
            future_to_key: dict[Future[RunRecord], tuple[str, str, int]] = {}
            for tier, inst, idx in pending:
                seed = sweep_config.base_seed + idx
                model_id = self._vertex_config.model_id(tier)
                future = pool.submit(
                    self._run_one,
                    tier=tier,
                    model_id=model_id,
                    instance=inst,
                    attempt_idx=idx,
                    seed=seed,
                    workspace_factory=workspace_factory,
                    workspace_cleanup=workspace_cleanup,
                )
                future_to_key[future] = (tier, inst.instance_id, idx)

            completed = skipped
            consecutive_grader_errors = 0
            last_grader_error = ""
            for future in as_completed(future_to_key):
                tier, instance_id, idx = future_to_key[future]
                completed += 1
                try:
                    record = future.result()
                    if record.grader_error:
                        consecutive_grader_errors += 1
                        last_grader_error = record.grader_error
                        logger.warning(
                            "[%d/%d] %s / %s / attempt %d → grader error (not saved): %s",
                            completed, len(work), tier, instance_id, idx,
                            record.grader_error,
                        )
                        if consecutive_grader_errors >= grader_circuit_limit:
                            for f in future_to_key:
                                f.cancel()
                            raise GraderCircuitBreaker(
                                last_grader_error, consecutive_grader_errors,
                            )
                    else:
                        consecutive_grader_errors = 0
                        self._store.save(record)
                        spent_usd += record.cost_usd
                        status = "resolved" if record.resolved else "not resolved"
                        logger.info(
                            "[%d/%d] %s / %s / attempt %d → %s ($%.4f, cumulative $%.2f)",
                            completed, len(work), tier, instance_id, idx,
                            status, record.cost_usd, spent_usd,
                        )
                    if spent_usd >= warn_threshold:
                        logger.warning(
                            "Spend $%.2f has reached %.0f%% of cap $%.2f",
                            spent_usd,
                            budget.warn_at_fraction * 100,
                            budget.max_spend_usd,
                        )
                except GraderCircuitBreaker:
                    raise
                except Exception:
                    logger.exception(
                        "Attempt (%s, %s, %d) failed", tier, instance_id, idx
                    )

    def _is_done(self, model_id: str, instance_id: str, attempt_idx: int) -> bool:
        try:
            self._store.get(model_id, instance_id, attempt_idx)
            return True
        except KeyError:
            return False

    def _run_one(
        self,
        tier: str,
        model_id: str,
        instance: SWEbenchInstance,
        attempt_idx: int,
        seed: int,
        workspace_factory: WorkspaceFactory,
        workspace_cleanup: Callable[[Path], None] | None = None,
    ) -> RunRecord:
        workspace_dir = workspace_factory(instance, attempt_idx, model_id)
        try:
            logger.info(
                "START %s / %s / attempt %d",
                tier, instance.instance_id, attempt_idx,
            )
            t0 = time.monotonic()
            scaffold: CLIScaffold | Scaffold
            if self._cli_scaffold is not None and tier not in VERTEX_TIERS:
                use_cli = True
                scaffold = self._cli_scaffold
            else:
                use_cli = False
                scaffold = self._scaffold
            attempt: AttemptResult = scaffold.run(
                instance=instance,
                workspace_dir=workspace_dir,
                model_id=model_id,
                seed=seed,
            )
            wall_clock_s = time.monotonic() - t0
            logger.info(
                "SCAFFOLD done %s / %s / attempt %d in %.0fs, grading …",
                tier, instance.instance_id, attempt_idx, wall_clock_s,
            )

            assert attempt.model_id == model_id, (
                f"Scaffold returned model_id={attempt.model_id!r} "
                f"but orchestrator expected {model_id!r}"
            )

            try:
                grade = safe_grade(instance, attempt.candidate_patch, self._grader)
                grader_error = ""
            except GraderError as exc:
                logger.error(
                    "GraderError for (%s, %s, %d): %s",
                    model_id, instance.instance_id, attempt_idx, exc,
                )
                grade = None
                grader_error = str(exc)

            record = RunRecord(
                model_id=attempt.model_id,
                instance_id=instance.instance_id,
                attempt_idx=attempt_idx,
                seed=seed,
                scaffold_version=attempt.scaffold_version,
                candidate_patch=attempt.candidate_patch,
                resolved=False if grade is None else grade.resolved,
                compiled=False if grade is None else grade.compiled,
                rejected_test_edit=False if grade is None else grade.rejected_test_edit,
                f2p_results=[] if grade is None else [
                    {"name": r.name, "passed": r.passed} for r in grade.f2p_results
                ],
                p2p_results=[] if grade is None else [
                    {"name": r.name, "passed": r.passed} for r in grade.p2p_results
                ],
                tokens_in=attempt.tokens_in,
                tokens_out=attempt.tokens_out,
                turns=attempt.turns,
                tool_calls=attempt.tool_calls,
                wall_clock_s=wall_clock_s,
                grader_error=grader_error,
                cli_scaffold=use_cli,
            )
            if attempt.cost_cents is not None:
                record.cost_usd = attempt.cost_cents / 100.0
            else:
                record.cost_usd = self._price_table.compute_cost(record)
            return record
        finally:
            if workspace_cleanup is not None:
                try:
                    workspace_cleanup(workspace_dir)
                except Exception:
                    logger.warning(
                        "Failed to clean up workspace %s", workspace_dir,
                        exc_info=True,
                    )
