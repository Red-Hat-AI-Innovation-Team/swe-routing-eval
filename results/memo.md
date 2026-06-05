# v1 Routing Eval — Result Memo

## Results

### OpenShift

| Label | Tier | Resolution rate | 95% CI | Cost/resolved bug | Underpowered |
|---|---|---|---|---|---|
| ★OpenShift sonnet | clean | 11.1% | (0.0%, 100.0%) | $14.09 | ⚠ YES |
| OpenShift cascade | clean | 11.1% | (11.1%, 11.1%) | $15.33 | no |
| ★OpenShift cascade | clean | 40.7% | (40.7%, 40.7%) | $16.94 | no |
| OpenShift cascade | clean | 40.7% | (40.7%, 40.7%) | $17.28 | no |
| OpenShift opus | clean | 33.3% | (0.0%, 100.0%) | $18.01 | ⚠ YES |
| OpenShift haiku | clean | 0.0% | (0.0%, 0.0%) | $inf | ⚠ YES |

## Do the segments separate?

Only one segment evaluated.

## Sample sizes

| Segment | Tier | Model | N instances |
|---|---|---|---|
| OpenShift | clean | claude-haiku-4-5@20251001 | 3 |
| OpenShift | clean | claude-opus-4-6 | 3 |
| OpenShift | clean | claude-sonnet-4-6 | 3 |

## Power sizing

One or more segments are underpowered. Point estimates from underpowered segments are inconclusive — do not interpret as evidence of no difference.

| Segment | Tier | Current N | Required N | Gap | Delta | Power |
|---|---|---|---|---|---|---|
| OpenShift | clean | 3 | 260 | 257 | — | — |
| OpenShift | clean | 3 | 260 | 257 | — | — |
| OpenShift | clean | 3 | 260 | 257 | — | — |

*(Delta and power level used are set by `--delta` and `--power` in `analyze_runs.py`. Fill in if different from defaults 0.10 / 0.80.)*

---

**Standing caveats (v1)**

1. **Testable-only.** Results cover only instances with runnable Docker test
   environments. Instances that could not be validated are excluded.
2. **Single fixed scaffold.** All models were evaluated with the same
   SWE-agent-style harness (`SCAFFOLD_VERSION`). Scaffold choice is a
   documented confound; a second-scaffold robustness check is deferred.
3. **Contamination tier.** Results are stratified by decontamination-overlap
   flag. The cleanest tier is reported first; contaminated instances are
   shown separately and should be interpreted with caution.
4. **Synthetic excluded.** All instances come from real merged pull requests.
   Synthetic or AI-generated instances are not included.
