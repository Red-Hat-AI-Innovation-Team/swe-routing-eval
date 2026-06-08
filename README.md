# swe-routing-eval

[![CI](https://github.com/Red-Hat-AI-Innovation-Team/swe-routing-eval/actions/workflows/ci.yml/badge.svg)](https://github.com/Red-Hat-AI-Innovation-Team/swe-routing-eval/actions/workflows/ci.yml)

Cost/quality routing evaluator for Go software-engineering benchmarks. Answers the
question: **for a given population of bugs, which Claude model tier (or escalation
cascade) resolves the most bugs per dollar?**

## How it fits together

```
SWE-benchify          →  swe-routing-eval
─────────────────────    ──────────────────────────────────────────────
Mine issue-linked PRs    Run each model against each instance (k times)
Validate with            Grade candidate patches via Multi-SWE-bench
  Multi-SWE-bench        Compute pass@1, cost-per-resolved-bug, cascade
Emit JSONL instances     Build Pareto frontier + output memo + plot
```

## Structure

| Path | Purpose |
|---|---|
| `src/swe_routing_eval/ingest.py` | SWE-benchify JSONL schema, validation, year filter |
| `src/swe_routing_eval/grading.py` | `Grader` protocol, `SwebenchifyGrader`, anti-reward-hacking |
| `src/swe_routing_eval/store.py` | SQLite run store — one row per (model, instance, attempt) |
| `src/swe_routing_eval/budget.py` | Dry-run cost projection and hard spend cap |
| `src/swe_routing_eval/cost.py` | Vertex pricing table, n-tier cascade cost model |
| `src/swe_routing_eval/stats.py` | Bootstrap CI, paired McNemar test, Connor (1987) power sizing |
| `src/swe_routing_eval/frontier.py` | Pareto frontier, memo renderer, frontier plot |
| `src/swe_routing_eval/orchestrator.py` | Sweep scheduler — parallel attempts, resume, budget guard, workspace cleanup |
| `src/swe_routing_eval/scaffold.py` | Fixed SWE-agent-style scaffold (bash + finish tools) |
| `scripts/eval_sweep.py` | Run or dry-run the model × instance × attempt matrix |
| `scripts/m0_coverage_check.py` | M0 gate: grade gold patches, verify all resolve |
| `scripts/analyze_runs.py` | Produce frontier, memo, and plot from a completed run store |
| `scripts/watch_sweep.py` | Live progress display while a sweep is running |
| `config/prices.json` | Vertex pricing at RH committed-use rates |

## Setup

```bash
pip install -e ".[dev]"      # core + dev tools (ruff, mypy, pytest)
pip install -e ".[analysis]" # + scipy / matplotlib for stats and plots
```

## Prerequisites

Vertex AI access with ADC configured; no `ANTHROPIC_API_KEY` needed.

```bash
gcloud auth application-default login

export CLOUD_ML_REGION=global
export ANTHROPIC_VERTEX_PROJECT_ID=<your-gcp-project>

# Pinned Model Garden IDs — use exact versioned strings, not aliases
export ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus-4-6
export ANTHROPIC_DEFAULT_SONNET_MODEL=claude-sonnet-4-6
export ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku-4-5@20251001
```

The grader imports `swebenchify.grader.grade` from the SWE-benchify repo.
Clone it and install it in the same environment:

```bash
pip install -e /path/to/SWE-benchify
```

## Usage

### 1. M0 coverage gate

Validate that gold patches resolve before running expensive inference.
Exits 0 on full pass, 1 on any failure.

```bash
python scripts/m0_coverage_check.py instances.jsonl

# Filter to a specific year
python scripts/m0_coverage_check.py instances.jsonl --year 2024
```

### 2. Dry-run cost projection

Estimate sweep cost before committing to inference.
Defaults: 450k input tokens / 7k output tokens per attempt (calibrated from
observed runs — actual cost scales with repo size and problem difficulty).

```bash
python scripts/eval_sweep.py instances.jsonl \
    --tiers haiku sonnet opus \
    --k 3 \
    --price-table config/prices.json \
    --max-spend-usd 200.00 \
    --dry-run
```

### 3. Run the sweep

```bash
python -u scripts/eval_sweep.py instances.jsonl \
    --tiers haiku sonnet opus \
    --k 3 \
    --price-table config/prices.json \
    --max-spend-usd 200.00 \
    --store runs.db \
    --workspace-root /tmp/swe-routing-eval-workspaces \
    2>&1 | tee sweep.log
```

The sweep is **resumable** — rerunning the same command skips already-completed
(model, instance, attempt) triples. If the grader fails on an instance,
a sentinel record is written so that instance is not retried indefinitely.

Workspaces are created lazily (one per worker thread) and cleaned up
automatically after each attempt finishes. Disk usage during a sweep is
bounded by `--workers`, not by total instance count. The shared repo clones
in `_cache/` are preserved across runs.

Filter to instances from a specific year:

```bash
python -u scripts/eval_sweep.py instances.jsonl --year 2024 \
    --tiers sonnet opus --k 3 \
    --price-table config/prices.json \
    --max-spend-usd 100.00 \
    --store runs.db \
    --workspace-root /tmp/workspaces
```

Watch live progress in a second terminal:

```bash
python scripts/watch_sweep.py --db runs.db --total 27
```

### 4. Analyze results

Produces `results/memo.md` (Pareto table + power sizing) and
`results/frontier.png` (cost vs. resolution rate chart).

```bash
python scripts/analyze_runs.py \
    --store runs.db \
    --instances instances.jsonl \
    --price-table config/prices.json \
    --tiers haiku sonnet opus \
    --cascade-tiers haiku sonnet opus \
    --output results/
```

Key flags:
- `--cascade-tiers` — ordered cheapest-first; emits cascade cost points for
  every adjacent pair and the full chain (analytical only, not shown in chart)
- `--delta` — minimum detectable resolution-rate difference (default 0.10)
- `--power` — target power for Connor sample-size formula (default 0.80)
- `--no-plot` — skip matplotlib if not installed

## Metrics

**pass@1** — probability a single attempt resolves a randomly chosen instance,
estimated as `total_resolved_attempts / total_attempts` (equivalent to the
per-instance average when k is constant).

**cost per resolved bug** — `mean_cost_per_attempt / pass@1`.

**cascade** — analytical routing strategy: try the cheapest tier first, escalate
only on failure. Cost and resolution rate are computed without additional runs:

```
p_cascade = p₁ + (1−p₁)·p₂ + (1−p₁)·(1−p₂)·p₃ + …
cost      = c₁ + (1−p₁)·c₂ + (1−p₁)·(1−p₂)·c₃ + …
```

**Required N** — Connor (1987) sample size for a paired McNemar test at
α = 0.05, 80% power, and a 10pp minimum detectable difference.

## Development

```bash
ruff check .    # lint
mypy src/       # type-check
pytest tests/   # test suite (181 tests, ~1s)
```
