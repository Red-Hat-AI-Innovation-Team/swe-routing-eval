# `swe-grade` Binary Contract

This document specifies the interface between this evaluator and the
SWE-benchify `swe-grade` binary (issue #2). Both sides must implement
this contract exactly; any divergence silently corrupts resolution rates.

---

## Invocation

```
swe-grade
```

The binary reads from **stdin** and writes to **stdout**. It takes no
positional arguments or flags.

---

## Input (stdin — JSON, one object)

```json
{
  "instance_id":    "etcd-io/etcd-12345",
  "repo":           "etcd-io/etcd",
  "base_commit":    "abc123def456",
  "test_patch":     "diff --git a/server/etcdserver/api_test.go ...",
  "image_name":     "swebench/etcd:abc123def456",
  "env_spec_hash":  "sha256:deadbeef...",
  "candidate_patch":"diff --git a/server/etcdserver/api.go ..."
}
```

| Field | Type | Description |
|---|---|---|
| `instance_id` | string | Unique instance identifier |
| `repo` | string | `owner/repo` on GitHub |
| `base_commit` | string | Git SHA at which the environment is built |
| `test_patch` | string | **Canonical** test patch — the unmodified tests from the PR |
| `image_name` | string | Docker image pre-built at `base_commit` |
| `env_spec_hash` | string | SHA256 of the environment spec; used to verify image integrity |
| `candidate_patch` | string | The model's proposed fix (unified diff) |

---

## Required binary behaviour

The binary **must**:

1. **Apply patches in order:**
   ```
   base_commit  →  candidate_patch  →  test_patch
   ```
   The `test_patch` is always applied on top of the candidate. This
   ensures grading always uses the canonical test surface, regardless of
   what the candidate patch does.

2. **Strip test-file modifications from `candidate_patch` before applying.**
   Any hunks touching `*_test.go` or `testdata/` paths are discarded. This
   is the grader-side enforcement of the anti-reward-hacking rule; the
   evaluator performs the same check in Python (`touches_test_files`) and
   rejects the attempt before calling the binary. Both checks must agree.

3. **Compile the patched tree** (`go build ./...`). Record `compiled=false`
   and return immediately if compilation fails — do not run tests.

4. **Run only the tests defined in `test_patch`** (the F2P and P2P sets).
   Do not run the full test suite.

5. **Record per-test pass/fail** for every test in both the F2P and P2P
   sets.

6. **Exit 0** on success (even if the candidate does not resolve), writing
   the JSON result to stdout. Exit non-zero only on infrastructure failure
   (Docker error, timeout, I/O error).

---

## Output (stdout — JSON, one object)

```json
{
  "resolved":  true,
  "compiled":  true,
  "f2p": [
    {"name": "TestApplyEntry",    "passed": true},
    {"name": "TestApplyEntryErr", "passed": false}
  ],
  "p2p": [
    {"name": "TestProcessEntry",  "passed": true}
  ],
  "telemetry": {
    "wall_clock_s": 47.3,
    "exit_code":    0
  }
}
```

| Field | Type | Description |
|---|---|---|
| `resolved` | bool | `true` iff **all** F2P pass **and** all P2P pass |
| `compiled` | bool | `true` iff `go build ./...` succeeded |
| `f2p` | array | Per-test outcome for every fail-to-pass test in `test_patch` |
| `p2p` | array | Per-test outcome for every pass-to-pass test in `test_patch` |
| `telemetry` | object | At minimum `wall_clock_s` (float) and `exit_code` (int) |

`f2p[i].name` and `p2p[i].name` must match the test function names as
parsed by `GoJSONParser` (see §GoJSONParser below).

---

## `resolved` semantics

```
resolved = compiled AND all(r.passed for r in f2p) AND all(r.passed for r in p2p)
```

The evaluator recomputes `resolved` from `f2p` and `p2p` after applying
quarantine filtering (`apply_quarantine` in `grading.py`). The binary's
top-level `resolved` field is informational and is not used directly.

---

## GoJSONParser

Test names in `f2p[i].name` and `p2p[i].name` must be produced by the
shared `GoJSONParser` module, factored standalone in SWE-benchify (issue #2
/ evaluator issue #25). Both the binary and the Python evaluator must use
the *identical* parser so "TestFoo passed" means the same thing on both
sides.

Until issue #25 is resolved, the evaluator trusts the binary's test names
verbatim.

---

## Error handling

| Condition | Binary behaviour |
|---|---|
| Docker image not found | Exit non-zero; stderr describes the failure |
| Compilation failure | Exit 0; `compiled=false`, `f2p=[]`, `p2p=[]` |
| Test timeout (> 600 s) | Exit 0; record timed-out tests as `passed=false` |
| Infrastructure error | Exit non-zero; evaluator raises `GraderError` |

---

## Version

This contract is pinned at **v1**. Breaking changes require a version bump
in `SubprocessGrader` and a corresponding update here.
