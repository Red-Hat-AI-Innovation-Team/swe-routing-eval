# swe-routing-eval

[![CI](https://github.com/Red-Hat-AI-Innovation-Team/swe-routing-eval/actions/workflows/ci.yml/badge.svg)](https://github.com/Red-Hat-AI-Innovation-Team/swe-routing-eval/actions/workflows/ci.yml)

**Dashboard: https://ai-innovation.team/swe-routing-eval/**

Cost/quality routing evaluator for software-engineering benchmarks. Answers the
question: **for a given population of bugs, which model tier (or escalation
cascade) resolves the most bugs per dollar?**

Supports **Go**, **Python**, and **Java** instances out of the box.
Language-specific behavior (system prompts, test file detection, grader
defaults) is configured declaratively in `languages.py` — adding a new
language is a single `register()` call, not forked code.

## End-to-end workflow

```
SWE-benchify                        swe-routing-eval
────────────────────────────────    ────────────────────────────────────
1. Mine issue-linked PRs            5. Import instances (JSONL)
2. Validate with Multi-SWE-bench    6. Run models against instances (k×)
3. Build & push Docker images       7. Grade candidate patches via Docker
4. Emit JSONL + env_spec.json       8. Analyze: Pareto frontier, cascade
```

### Preparing instances from SWE-benchify

SWE-benchify pipeline workflows (e.g. `java-pipeline.yml`,
`python-pipeline.yml`) produce two key artifacts per repo:

- **`{repo_slug}-task-instances.jsonl`** — validated instances with patches,
  test patches, and metadata
- **`env_spec.json`** — build/test environment spec (language version, package
  manager, test command, pre-install steps)

To use these instances in swe-routing-eval:

1. **Copy the instances JSONL** into this repo (e.g. `instances-java.jsonl`)
   and optionally append to `instances.jsonl`.

2. **Copy the env spec** to `config/` named by its full SHA-256 hash:

   ```bash
   # The hash is the env_spec_hash field in the instances JSONL
   cp env_spec.json config/<full-env-spec-hash>.json
   ```

   The grader looks up env specs by filename stem, so the file must be named
   `<hash>.json` (not `env_spec.json` or a truncated hash).

3. **Docker images** (optional but recommended): If the pipeline built and
   pushed images (the `image_name` field is populated), the grader pulls them
   directly. If `image_name` is empty, the grader falls back to building a
   container from the env spec at grade time — this works but is slower and
   requires `--env-specs-dir`.

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
| `src/swe_routing_eval/languages.py` | Declarative language config registry (system prompts, test patterns, defaults) |
| `src/swe_routing_eval/llm.py` | LLM client abstraction (Anthropic Vertex, Cursor CLI) |
| `src/swe_routing_eval/scaffold.py` | Fixed SWE-agent-style scaffold (bash + finish tools) |
| `scripts/eval_sweep.py` | Run or dry-run the model x instance x attempt matrix |
| `scripts/m0_coverage_check.py` | M0 gate: grade gold patches, verify all resolve |
| `scripts/analyze_runs.py` | Produce frontier, memo, and plot from a completed run store |
| `scripts/watch_sweep.py` | Live progress display while a sweep is running |
| `scripts/export_dashboard_data.py` | Export `runs.db` + instances to `docs/data.json` for the dashboard |
| `scripts/regrade.py` | Re-grade stored patches against current Docker images |
| `docs/index.html` | Static GitHub Pages dashboard (Plotly.js, vanilla JS) |
| `config/prices.json` | Model pricing table |
| `config/<hash>.json` | Env spec files keyed by SHA-256 hash (used by grader) |
| `instances*.jsonl` | Instance files — per-language or combined |

## Setup

```bash
pip install -e ".[dev]"      # core + dev tools (ruff, mypy, pytest)
pip install -e ".[analysis]" # + scipy / matplotlib for stats and plots
```

The grader imports `swebenchify.grader.grade` from the SWE-benchify repo.
Install it in the same environment:

```bash
pip install -e /path/to/SWE-benchify
```

## Prerequisites

### Docker

Docker must be running for grading. The grader builds containers to run
tests against candidate patches. Verify with:

```bash
docker image ls   # should succeed without I/O errors
```

If Docker Desktop crashes during grading (common with large Maven builds),
check `docker info | grep "Total Memory"` — the default 8GB VM allocation
may be insufficient. Increase it in Docker Desktop settings.

### Anthropic models via Vertex AI

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

### GPT models via Cursor CLI

GPT models (e.g. `gpt-5.3-codex-xhigh`, `gpt-5.4-xhigh`) run through the
Cursor `agent` CLI rather than Vertex AI. The CLI doesn't report reasoning
tokens, so costs for xhigh reasoning runs are undercounted by default.

To get accurate per-attempt costs including reasoning tokens, set a Cursor
dashboard session token:

```bash
export CURSOR_SESSION_TOKEN='<WorkosCursorSessionToken cookie value>'
```

Get this from your browser: open `cursor.com`, DevTools > Application >
Cookies, copy the `WorkosCursorSessionToken` value. The token is a JWT
that expires ~60 days from login.

When set, each CLI run queries the Cursor dashboard API for actual billed
costs (`totalCents`) and overrides the token-based estimate. If unset, a
warning is printed and costs fall back to CLI-reported usage (which excludes
reasoning tokens).

**Important:** GPT sweeps must use `--workers 1`. Concurrent runs of the
same model produce overlapping time windows, causing cost events to be
misattributed between attempts.

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

#### Sweep flags reference

| Flag | Default | Description |
|---|---|---|
| `instances.jsonl` (positional) | *required* | SWE-benchify instances file |
| `--tiers` | `sonnet` | Model tiers to evaluate (space-separated) |
| `--k` | `3` | Attempts per (model, instance) pair |
| `--price-table` | *required* | JSON pricing file (see `config/prices.json`) |
| `--max-spend-usd` | `100.00` | Hard spend cap — sweep stops when reached |
| `--store` | `runs.db` | SQLite database for results |
| `--workspace-root` | system temp | Directory for repo checkouts |
| `--workers` | `4` | Concurrent attempts (use `1` for CLI/GPT tiers) |
| `--year` | all | Filter instances by fix_merge_date year(s) |
| `--env-specs-dir` | none | Directory of `<hash>.json` env spec files |
| `--dry-run` | off | Cost projection only, no inference |

#### Tier names

**Vertex tiers** (`opus`, `sonnet`, `haiku`) are symbolic — they resolve to
model IDs via the `ANTHROPIC_DEFAULT_*_MODEL` environment variables.

**CLI tiers** (e.g. `claude-4.6-sonnet-medium-thinking`,
`claude-4.6-opus-max-thinking`, `gpt-5.4-xhigh`, `gpt-5.3-codex-xhigh`)
are passed as literal strings. They must have a matching entry in the
`--price-table` JSON file. These run through the Cursor `agent` CLI.

#### Docker images vs env specs for grading

The grader needs a Docker environment to run tests. It resolves this in
order of precedence:

1. **Pre-built image** — if the instance has a non-empty `image_name` field,
   the grader pulls and uses it directly. This is the fastest path.

2. **Env spec fallback** — if `image_name` is empty but `--env-specs-dir` is
   provided and contains a `<env_spec_hash>.json` file matching the instance,
   the grader builds a Docker container on the fly from the spec.

3. **Failure** — if neither is available, the grader cannot build a container.
   The run records `compiled=0` with empty test results.

For instances without pre-built images (e.g. freshly mined Java instances
before the image build workflow runs), you **must** pass `--env-specs-dir`:

```bash
python -u scripts/eval_sweep.py instances-java.jsonl \
    --tiers claude-4.6-sonnet-medium-thinking gpt-5.4-xhigh \
    --k 3 \
    --price-table config/prices.json \
    --workers 1 \
    --env-specs-dir config \
    --store runs.db \
    --workspace-root /tmp/swe-routing-eval-java \
    2>&1 | tee sweep.log
```

#### Resumability

The sweep is **resumable** — rerunning the same command skips already-completed
(model, instance, attempt) triples. If the grader fails on an instance,
a sentinel record is written so that instance is not retried indefinitely.

Workspaces are created lazily (one per worker thread) and cleaned up
automatically after each attempt finishes. Disk usage during a sweep is
bounded by `--workers`, not by total instance count. The shared repo clones
in `_cache/` are preserved across runs.

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

### 5. Re-grade existing runs

Re-run the grading pipeline on stored patches without re-running inference.
Useful when grading failed due to Docker issues, missing images, or
environment problems at the time of the original run.

```bash
# Preview which runs will be regraded (no changes)
python3 scripts/regrade.py --dry-run \
    --where "instance_id LIKE '%hypershift%' AND cli_scaffold = 1"

# Re-grade all non-compiled CLI runs
python3 scripts/regrade.py --workers 4 \
    --where "compiled = 0 AND cli_scaffold = 1"

# Re-grade a specific model
python3 scripts/regrade.py --workers 4 \
    --where "model_id = 'claude-4.6-opus-max-thinking' AND compiled = 0"
```

The `--where` flag accepts any valid SQL WHERE clause against the `runs`
table. The script backs up the database to `runs.db.bak-regrade` before
writing (skip with `--no-backup`).

**Prerequisites:** Docker must be running and healthy (`docker image ls`
should succeed without errors). The grading images must be pullable — if
`docker image ls` shows I/O errors, restart Docker Desktop first.

After regrading, regenerate the dashboard data:

```bash
python3 scripts/export_dashboard_data.py
```

### 6. Dashboard

A static dashboard served via GitHub Pages. Regenerate the data file
after new runs and commit it:

```bash
python3 scripts/export_dashboard_data.py
```

This reads `runs.db` and `instances*.jsonl` files, then writes
`docs/data.json`. The dashboard at `docs/index.html` loads this file
client-side — no server required.

To preview locally:

```bash
python3 -m http.server 8765 --directory docs
open http://localhost:8765
```

To publish, commit and push `docs/data.json`, then enable GitHub Pages
in repo settings (source: main branch, `/docs` folder).

## Troubleshooting

### Grading fails with `compiled=0` and empty test results

The grader couldn't build a Docker container. Check:

1. **Is Docker running?** `docker image ls` should return without errors.
2. **Does the instance have `image_name` set?** If not, you need
   `--env-specs-dir` pointing to a directory with `<env_spec_hash>.json`.
3. **Is the env spec file named correctly?** It must be the *full* SHA-256
   hash as the filename (e.g. `89cac2a28dd2...685b.json`), not a truncated
   or prefixed name.

### Docker Desktop crashes during grading

Java/Maven builds can consume significant memory. The Docker Desktop VM
defaults to 8GB. If builds OOM:

- Increase Docker Desktop memory (Settings > Resources)
- Free build cache: `docker builder prune`
- Free unused images: `docker image prune`

Check current usage with `docker system df`.

### GPT sweep cost attribution is wrong

GPT models via Cursor CLI require `--workers 1`. Concurrent runs produce
overlapping time windows, causing the dashboard API to misattribute costs
between attempts. Also ensure `CURSOR_SESSION_TOKEN` is set for accurate
reasoning-token costs.

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
pytest tests/   # test suite
```
