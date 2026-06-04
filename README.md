# swe-routing-eval

[![CI](https://github.com/Red-Hat-AI-Innovation-Team/swe-routing-eval/actions/workflows/ci.yml/badge.svg)](https://github.com/Red-Hat-AI-Innovation-Team/swe-routing-eval/actions/workflows/ci.yml)

Cost/quality model routing evaluator — the consumer side of SWE-benchify. Ingests
validated, segmentation-tagged Go instances, runs a cost-tiered Claude model panel
against them through Vertex AI, grades the results, and produces the per-segment
Pareto frontier + memo that informs routing policy.

See [`docs/PLAN.md`](docs/PLAN.md) for the full engineering design.

## Structure

| Path | Purpose |
|---|---|
| `src/swe_routing_eval/ingest.py` | SWE-benchify JSONL schema + fail-loud validation |
| `src/swe_routing_eval/grading.py` | Grading engine types, `Grader` protocol, anti-reward-hacking |
| `src/swe_routing_eval/store.py` | SQLite run store (patch · grade · telemetry · cost per attempt) |
| `src/swe_routing_eval/budget.py` | Dry-run cost projection + spend cap |
| `src/swe_routing_eval/cost.py` | Vertex pricing table (RH rates) + cost-per-resolved-bug |
| `src/swe_routing_eval/stats.py` | Bootstrap CI, paired McNemar, Connor power sizing |
| `src/swe_routing_eval/frontier.py` | Per-segment Pareto frontier + v1 memo |

## Setup

```bash
pip install -e ".[dev]"      # core + dev tools
pip install -e ".[analysis]" # + scipy / matplotlib for Epic B
```

## Development

```bash
ruff check .       # lint
mypy src/          # type-check
pytest tests/ -v   # test
```

## Prerequisites

Vertex AI access with ADC configured; no `ANTHROPIC_API_KEY` required.

```bash
export CLAUDE_CODE_USE_VERTEX=1
export CLOUD_ML_REGION=us-east5
export ANTHROPIC_VERTEX_PROJECT_ID=<your-project>
# Tier IDs pinned to RH Model Garden — see docs/PLAN.md §WS-2
export ANTHROPIC_DEFAULT_OPUS_MODEL=<rh-opus-id>
export ANTHROPIC_DEFAULT_SONNET_MODEL=<rh-sonnet-id>
export ANTHROPIC_DEFAULT_HAIKU_MODEL=<rh-haiku-id>
```
