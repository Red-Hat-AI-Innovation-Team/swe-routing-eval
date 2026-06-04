# Engineering Plan — Evaluator: Cost/Quality Model Routing (the consumer side)

**Repo (proposed):** `Red-Hat-AI-Innovation-Team/swe-routing-eval`
**Status:** Draft for review · **Owner:** Ari
**Relationship to the producer:** SWE-benchify emits validated, segmentation-tagged Go instances. This repo *consumes* that JSONL, runs a cost-tiered model panel against it, grades the results, and produces the per-segment Pareto frontier + memo that informs routing policy. This is the artifact leadership actually reads.

---

## 0. Boundary

**In scope (this repo):** ingest SWE-benchify JSONL; a deterministic grading engine; a Vertex-backed model-invocation layer under a fixed scaffold; the eval orchestrator + run store; the cost model; paired per-segment statistics; the v1 deliverable (two-segment Pareto frontier + one-page memo).

**Out of scope (→ SWE-benchify):** instance mining, environment discovery, validation, emission. This repo never collects PRs or builds instances; it only consumes them.

**Deferred to the full version:** live dashboard, what-if sliders, ROI economics (`V` / `C_fp`), hierarchical mixed-effects model, org-wide segmentation, continuous freshness, drift monitoring. v1 holds to the shortest-path-to-trustworthy-result framing from the design doc: two contrasting segments (`kubectl` vs `etcd`), 2–3 cost tiers, a static deliverable.

**Non-negotiables carried from the design doc:** execution-based grading; anti-reward-hacking (locked tests, grade on canonical `test_patch`); flake quarantine at grade time; contamination-tier stratification; real-PR instances only; paired statistics with CIs and explicit power.

---

## 1. Inputs this repo builds on (cross-repo contracts)

1. **SWE-benchify JSONL.** Each instance carries the base `SWEbenchInstance` fields plus the additive columns the producer now emits: `repo_language, product, fix_merge_date, provenance, link_confidence, n_fail_to_pass, patch_lines, files_touched, cross_file, env_spec_hash, image_name, compiled`, plus the validation-evidence columns (`n_runs`, flake counts, quarantined tests) and the decontamination-overlap flag. This schema is the contract; the evaluator pins it and fails loudly on drift.
2. **The shared Go log parser** (`GoJSONParser`), factored standalone in the producer per option A. The evaluator imports the *identical* module so grading and validation cannot disagree about what "test X passed" means.
3. **Multi-SWE-bench** as an independent grading oracle (the #1 coverage check), not necessarily as the production grader — see §2.
4. **Vertex AI** as the model provider (no `ANTHROPIC_API_KEY`); RH's Model Garden defines which Claude tiers are available.

---

## 2. Key decisions

**Grading engine — reuse the producer's deterministic path (recommended).** Grading a model's candidate patch is structurally identical to validating the gold patch: apply candidate + canonical `test_patch` at `base_commit`, run, check F2P/P2P. Now that producer WS-B made SWE-benchify's Go validation deterministic (Docker + `GoJSONParser`), the cleanest grader is to **reuse that exact execution path**, swapping the candidate patch in for gold. Validation and grading become the *same code*, which eliminates parser/harness drift entirely — strictly better than sharing only the parser. This refines the producer plan's §2 assumption (which named Multi-SWE-bench as the downstream grader); we keep Multi-SWE-bench in a sharper role:

- **Production grader:** SWE-benchify's deterministic Docker validation path, exposed as an importable `grade(instance, candidate_patch) -> {resolved, f2p, p2p, compiled, telemetry}` API. (Small producer-side addition if not already exposed; tracked as issue 2.)
- **Independent oracle:** Multi-SWE-bench, used in the M0 gold-validation gate (and periodically thereafter) to confirm gold resolves identically through a *second, independent* harness. Two harnesses agreeing on gold is a strong validity argument; divergence is an early warning.

This stays consistent with SWE-benchify's producer charter: the evaluator owns orchestration, model inference, and scoring policy; it merely calls the producer's low-level "run tests against this patch" primitive as a library.

**Fixed scaffold.** Hold the agent harness, tools, prompt, and `max_turns` constant across every model so we compare *models*, not scaffolds. The scaffold is a documented confound, not a variable. v1 uses one off-the-shelf SWE-agent-style scaffold; a second-scaffold robustness check is deferred.

**Sampling.** k = 3–5 attempts per `(model, instance)`; report pass@1 (unbiased per-attempt) and per-instance `p̂ = c/n`. More *instances* beats more *attempts* for population estimates, so k stays small; attempts exist to estimate per-instance probability and to feed the discordance estimate for power sizing.

**Provider.** Models invoked through Vertex (§ WS-2), tiers pinned to RH Model Garden IDs. Cost axis uses **Vertex pricing at RH's rates**, not Anthropic list prices.

---

## 3. Workstreams

Sizes relative (S/M/L); dependencies noted.

### WS-1 — Grading engine + anti-reward-hacking · M

- Wrap the producer's `grade()` API; map its output to a binary per-attempt outcome: resolved ⇔ all F2P pass **and** all P2P pass, on the canonical re-applied `test_patch`.
- Anti-reward-hacking: discard the model's edits to any test path, re-apply the instance's canonical `test_patch`, grade on the original tests. Reject attempts that touched `*_test.go` / `testdata/`.
- Carry `compiled` through as a distinct non-resolved outcome (clean reject vs test-fail).
- Apply the producer's flake-quarantine metadata at grade time: never count a quarantined test toward F2P; restrict etcd F2P to unit packages.
- **Acceptance:** feeding gold patches through `grade()` resolves 100% of instances and matches the recorded F2P/P2P; an attempt that edits tests is rejected.
- **Depends on:** producer `grade()` API (issue 2), M0 oracle gate.

### WS-2 — Model invocation layer (Vertex) + fixed scaffold · M

- Vertex provider config, no `ANTHROPIC_API_KEY`: `CLAUDE_CODE_USE_VERTEX=1`, `CLOUD_ML_REGION`, `ANTHROPIC_VERTEX_PROJECT_ID`, ADC; tiers pinned via `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` to RH-approved Model Garden IDs (without pinning, the `opus` alias resolves to an older version on Vertex).
- Confirm the approved tier set = the panel; if a non-Claude cheap tier is wanted (e.g. Granite), wire it as a separate provider adapter.
- Fixed scaffold: one SWE-agent-style harness, tools/prompt/`max_turns` held constant; the model is the only variable.
- k attempts per `(model, instance)` with seed logging; per-attempt telemetry: tokens in/out, turns, tool calls, wall-clock.
- **Acceptance:** a single instance runs end-to-end through one tier on Vertex, producing a candidate patch + full telemetry, with no `ANTHROPIC_API_KEY` present.

### WS-3 — Orchestrator + run store + eval budgeting · M–L

- Schedule the `model × instance × attempt` matrix with bounded concurrency; resumable checkpoints (mirror SWE-benchify's resumability) so a failed run doesn't restart from zero.
- Run store: persist every attempt's candidate patch, grade, telemetry, cost, seed, scaffold version, and model ID — keyed for full reproducibility.
- **Eval-run budgeting:** the frontier-tier runs (Opus × k × N × 2 repos, multi-turn) dominate spend; estimate and cap total eval $ up front, and surface a dry-run cost projection before a full sweep. Cheap-tier sweeps are nearly free; the budget gate is really about the frontier tier.
- **Acceptance:** a full panel sweep over a small instance set completes, is resumable mid-run, and the run store reproduces any single attempt.
- **Depends on:** WS-1, WS-2.

### WS-4 — Cost model · S

- Per-attempt cost = telemetry tokens × **Vertex price table** (parameterized; defaults to RH committed-use rates, never hard-coded list prices).
- Per segment/tier: expected cost per resolved bug = `E[cost] / p̂`; compute the two-tier **cascade** point (`p = p_cheap + (1−p_cheap)·p_frontier`, `E[c] = E[c_cheap] + (1−p_cheap)·E[c_frontier]`) from the same runs at no extra eval cost.
- **Acceptance:** every attempt has a dollar cost; per-segment frontier and cascade points compute from the run store.

### WS-5 — Statistics & power · M

- Per-segment resolution rate with bootstrap CI over instances (not attempts — attempts are nested); pass@1, pass@k, per-instance `p̂`.
- Paired comparison across tiers within a segment (McNemar / paired bootstrap) — never unpaired.
- Power sizing (Connor): from the M1 pilot, estimate discordance `p_d` and size the instances-per-segment needed to detect the tier gap leadership cares about; flag segments below threshold as underpowered rather than reporting a point estimate.
- Stratify by contamination tier; lead with the cleanest.
- **Acceptance:** a per-segment comparison emits effect size + CI + the n behind it, and an explicit "underpowered: needs N more" where applicable.
- **Depends on:** WS-3 (runs), WS-4 (cost for the frontier).

### WS-6 — v1 deliverable · S

- Per-segment Pareto frontier (x = cost per resolved bug, y = resolution rate): one point per tier plus the cascade point, with CIs, for kubectl and etcd.
- One-page memo: the result, the n per segment, the CIs, and the four standing caveats (testable-only; single fixed scaffold; contamination tier; synthetic excluded).
- Static artifact — no dashboard, no sliders, no ROI model in v1.
- **Acceptance:** a reviewer can read the frontier + memo and see whether the two segments separate, with confidence bounds.

---

## 4. Interfaces & data model

| Surface | Definition |
|---|---|
| Input | SWE-benchify JSONL (§1.1); pinned, validated on ingest, fail-loud on schema drift |
| Shared parser | Import `GoJSONParser` from the producer (versioned dependency) |
| Grading | `grade(instance, candidate_patch) -> {resolved, f2p, p2p, compiled, telemetry}` (producer-exposed) |
| Oracle | Multi-SWE-bench eval entrypoint, used for the gold-validation gate |
| Provider | Vertex env config + pinned tier IDs (WS-2) |
| Run store | one row per `(model, instance, attempt)`: patch, grade, telemetry, cost, seed, scaffold_version, model_id |
| Output | frontier data (per segment/tier/cascade, with CIs) + memo |

---

## 5. Sequencing & milestones

- **M0 — Coverage gate (this is #1).** Run gold patches for ~5 kubectl + ~5 etcd instances through Multi-SWE-bench's harness; confirm gold resolves and F2P matches the producer's records. Wire the shared parser. **Gate:** gold validates identically through both the producer path and the Multi-SWE-bench oracle. No model access required.
- **M1 — Single-tier pilot.** WS-1 + WS-2 + minimal WS-3: one tier, both segments, k attempts, graded, telemetry + cost logged. **Gate:** end-to-end on Vertex; produces the discordance/`p̂` estimates that feed WS-5 power sizing.
- **M2 — Full sweep.** Panel × both segments at the power-sized N from M1; run store populated within the eval budget. **Gate:** both segments reach power, or an honest "underpowered, needs N more."
- **M3 — Result.** WS-4 + WS-5 + WS-6: frontier + memo → the v1 trustworthy result. **Gate:** go/no-go input for the full-org version.

Critical path: M0 → WS-1/WS-2 (parallel) → WS-3 → M1 pilot → (power-sized) M2 → WS-5/WS-6.

### 5.1 Epic structure

Two epics, cut by risk and definition-of-done, mirroring the producer.

**Epic A — Grading & execution** — WS-1, WS-2, WS-3 (+ M0 gate). "Can we faithfully grade Go model patches and run the panel on Vertex within budget?" Essentially all technical and cost risk lives here: Multi-SWE-bench/producer grading fidelity, the Vertex path, and eval spend. **Exit = M2:** a complete, reproducible, budgeted run store over both segments.

**Epic B — Measurement & reporting** — WS-4, WS-5, WS-6. "Can we turn graded runs into a defensible leadership artifact?" Lower technical risk; this is where the actual value is realized. **Exit = M3:** frontier + memo with CIs and power flags.

**Ownership:** Epic A pairs naturally with whoever owns the producer's grading internals (the `grade()` exposure is a shared seam); Epic B is the analysis/leadership-facing half.

**Cross-repo edge:** the `grade()` API (issue 2) is a small addition on the *producer* side that this repo depends on. It's the one place work reaches back into SWE-benchify; everything else here consumes only its output.

---

## 6. Risks

- **Grading fidelity / coverage gap.** If Multi-SWE-bench or the producer path can't grade some kubectl/etcd packages, the eval stalls. Mitigation: M0 gates this on gold before any model runs; two independent harnesses must agree.
- **Parser/harness drift.** Mitigation: reuse the producer's execution path wholesale (§2), not a reimplementation; pin the parser version.
- **Scaffold confound.** v1 measures model × one scaffold. Mitigation: documented caveat; second-scaffold check deferred.
- **etcd flakiness at grade time.** Same risk as validation, now per-attempt. Mitigation: inherit the producer's quarantine metadata; unit-package F2P only.
- **Eval cost.** Frontier-tier × k × N × multi-turn can be expensive on Vertex. Mitigation: dry-run projection + budget cap (WS-3); small k; instances-over-attempts.
- **Power / yield.** If the fresh slice is small, segments underpower. Mitigation: M1 sizes N; report underpowered honestly; widen within archetype on the producer side.
- **Vertex availability/pricing.** Panel is bounded by approved tiers; cost numbers depend on RH rates. Mitigation: confirm Model Garden access early; parameterize the price table.
- **Tests-pass ≠ correct.** Mitigation: human spot-audit a sample per segment before reporting.

---

## 7. Issue list (fileable; dependency-ordered)

1. **M0 coverage check:** run gold patches for ~5 kubectl + ~5 etcd instances through Multi-SWE-bench; confirm gold resolves and F2P matches producer records. *(No model access; this is #1.)*
2. **Producer dependency:** expose SWE-benchify's deterministic validation as an importable `grade(instance, candidate_patch)` API. *(Small change on the producer side.)*
3. Ingest + schema-pin SWE-benchify JSONL; fail-loud on drift; import shared `GoJSONParser`.
4. Grading engine over `grade()`; binary resolved outcome; carry `compiled`.
5. Anti-reward-hacking: test-path lock, canonical `test_patch` re-application, reject test edits.
6. Flake-quarantine application at grade time; etcd unit-package F2P restriction.
7. Vertex provider config (no API key) + tier pinning to RH Model Garden IDs.
8. Fixed scaffold integration; tools/prompt/`max_turns` held constant; seed logging.
9. k-attempt sampling + per-attempt telemetry (tokens, turns, tool calls, wall-clock).
10. Orchestrator over `model × instance × attempt`; bounded concurrency; resumable checkpoints.
11. Run store (patch, grade, telemetry, cost, seed, scaffold_version, model_id); reproducibility.
12. Eval-run budgeting: dry-run cost projection + spend cap, frontier-tier gated.
13. Cost model: per-attempt Vertex pricing table (RH rates) + expected-cost-to-resolve + cascade point.
14. Statistics: per-segment rate + bootstrap CI; paired McNemar/bootstrap across tiers.
15. Power sizing (Connor) from M1 pilot; underpowered flagging + required-N.
16. v1 deliverable: per-segment Pareto frontier (tiers + cascade, CIs) + one-page memo with the four caveats.
17. Human spot-audit harness: sample resolved attempts per segment to check passing-but-wrong.
