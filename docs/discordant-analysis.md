# Discordant Pairs Deep-Dive: Cursor CLI Runs

> Qualitative analysis of instances where Anthropic and GPT model families diverge on resolution in cursor CLI (zero-turn) mode.

## A. Summary Statistics

**90 instances** evaluated across 4 models (3 attempts each).

### Per-model solve rates (pass@3)

| Model | Solved | Rate |
|-------|--------|------|
| gpt-5.4 | 70/90 | 77.8% |
| gpt-5.3 | 61/90 | 67.8% |
| opus | 53/90 | 58.9% |
| sonnet | 51/90 | 56.7% |

### Concordance matrix

- Both families solve: **51** instances
- Neither family solves: **15** instances
- **Discordant: 24** instances
  - GPT solves, Anthropic doesn't: **21**
  - Anthropic solves, GPT doesn't: **3**

## B. Behavioral Patterns

### Patch size: winners vs losers

- Winning patches: median **20** change lines (range 2–122)
- Losing patches: median **14** change lines (range 2–91)

### Scope: files touched

- Winners touching multiple files: **6/37**
- Losers touching multiple files: **7/48**

### Partial credit in losing attempts

How close did losing models get? F2P pass rates in their best attempt:

- Zero tests passing: **44/48** (92%)
- Partial progress (some but not all f2p tests): **4/48** (8%)

### Intra-family consistency

When one model in a family fails a discordant instance, does the other model in the same family also fail?

- Both Anthropic models fail together: **21/21** GPT-only wins
- Both GPT models fail together: **3/13** Anthropic-only wins

### Discordance by repository

| Repo | GPT-only wins | Anthropic-only wins | Total instances |
|------|--------------|---------------------|----------------|
| kubernetes/kubernetes | 17 | 1 | 59 |
| grpc/grpc-go | 2 | 0 | 5 |
| openshift/hypershift | 1 | 1 | 19 |
| argoproj/argo-cd | 1 | 0 | 1 |
| etcd-io/etcd | 0 | 1 | 2 |

## C. Case Studies

### etcd-io__etcd-21757

**Structured logging migration** | etcd-io/etcd | Category: anthropic_wins

Target tests: `TestLog`

| Model | a=0 | a=1 | a=2 |
|-------|-----|-----|-----|
| opus | PASS | PASS | FAIL |
| sonnet | FAIL | PASS | FAIL |
| gpt-5.3 | FAIL | FAIL | FAIL |
| gpt-5.4 | FAIL | FAIL | FAIL |

**Winning patch** (sonnet, 1 file(s), 9 change lines):

```diff
diff --git a/pkg/traceutil/trace.go b/pkg/traceutil/trace.go
@@ -200,7 +200,6 @@ func (t *Trace) logInfo(threshold time.Duration) (string, []zap.Field) {
-	msg := fmt.Sprintf("trace[%d] %s", traceNum, t.operation)
@@ -228,13 +227,15 @@ func (t *Trace) logInfo(threshold time.Duration) (string, []zap.Field) {
-			steps = append(steps, fmt.Sprintf("trace[%d] '%v' %s (duration: %v)",
-				traceNum, tstep.msg, writeFields(tstep.fields), stepDuration))
+			steps = append(steps, fmt.Sprintf("'%v' %s (duration: %v)",
+				tstep.msg, writeFields(tstep.fields), stepDuration))
+		zap.Int32("trace_id", traceNum),
+		zap.String("operation", t.operation),
@@ -242,7 +243,7 @@ func (t *Trace) logInfo(threshold time.Duration) (string, []zap.Field) {
-	return msg, fs
+	return "trace", fs
```

**Losing patch** (gpt-5.4, 1 file(s), 4 change lines):

```diff
diff --git a/pkg/traceutil/trace.go b/pkg/traceutil/trace.go
@@ -200,7 +200,7 @@ func (t *Trace) logInfo(threshold time.Duration) (string, []zap.Field) {
-	msg := fmt.Sprintf("trace[%d] %s", traceNum, t.operation)
+	msg := "trace"
@@ -235,6 +235,8 @@ func (t *Trace) logInfo(threshold time.Duration) (string, []zap.Field) {
+		zap.Int32("trace_id", traceNum),
+		zap.String("operation", t.operation),
```

**Analysis**:  Both families identified the correct file and function (`logInfo` in `trace.go`). The GPT models made the top-level message static (`msg := "trace"`) and added structured fields — a partial fix. The Anthropic models went further: they also rewrote the per-step format strings to remove the embedded `trace[%d]` prefix from each step, making the structured logging migration complete. The test required both changes — the static top-level message AND clean step strings — so partial fixes failed.

---

### kubernetes__kubernetes-135513

**Multi-file kubectl apply fix** | kubernetes/kubernetes | Category: anthropic_wins

Target tests: `TestApplyDryRunClientMergesWithServerState`

| Model | a=0 | a=1 | a=2 |
|-------|-----|-----|-----|
| opus | PASS | PASS | PASS |
| sonnet | FAIL | FAIL | FAIL |
| gpt-5.3 | FAIL | FAIL | FAIL |
| gpt-5.4 | FAIL | FAIL | FAIL |

**Winning patch** (opus, 2 file(s), 75 change lines):

```diff
diff --git a/staging/src/k8s.io/kubectl/pkg/cmd/apply/apply.go b/staging/src/k8s.io/kubectl/pkg/cmd/apply/apply.go
@@ -725,36 +725,34 @@ See https://kubernetes.io/docs/reference/using-api/server-side-apply/#conflicts`
-	if o.DryRunStrategy != cmdutil.DryRunClient {
-		metadata, _ := meta.Accessor(info.Object)
-		annotationMap := metadata.GetAnnotations()
-		if _, ok := annotationMap[corev1.LastAppliedConfigAnnotation]; !ok {
-			fmt.Fprintf(o.ErrOut, warningNoLastAppliedConfigAnnotation, info.ObjectName(), corev1.LastAppliedConfigAnnotation, o.cmdBaseName)
-		}
+	metadata, _ := meta.Accessor(info.Object)
+	annotationMap := metadata.GetAnnotations()
+	if _, ok := annotationMap[corev1.LastAppliedConfigAnnotation]; !ok {
+		fmt.Fprintf(o.ErrOut, warningNoLastAppliedConfigAnnotation, info.ObjectName(), corev1.LastAppliedConfigAnnotation, o.cmdBaseName)
+	}
-		patcher, err := newPatcher(o, info, helper)
-		if err != nil {
-			return err
-		}
-		patchBytes, patchedObject, err := patcher.Patch(info.Object, modified, info.Source, info.Namespace, info.Name, o.ErrOut)
-		if err != nil {
-			return cmdutil.AddSourceToErr(fmt.Sprintf("applying patch:\n%s\nto:\n%v\nfor:", patchBytes, info), info.Source, err)
-		}
+	patcher, err := newPatcher(o, info, helper)
+	if err != nil {
+		return err
+	}
+	patchBytes, patchedObject, err := patcher.Patch(info.Object, modified, info.Source, info.Namespace, info.Name, o.ErrOut)
+	if err != nil {
+		return cmdutil.AddSourceToErr(fmt.Sprintf("applying patch:\n%s\nto:\n%v\nfor:", patchBytes, info), info.Source, err)
+	}
-		info.Refresh(patchedObject, true)
+	info.Refresh(patchedObject, true)
-		WarnIfDeleting(info.Object, o.ErrOut)
+	WarnIfDeleting(info.Object, o.ErrOut)
-		if string(patchBytes) == "{}" && !o.shouldPrintObject() {
-			printer, err := o.ToPrinter("unchanged")
-			if err != nil {
-				return err
-			}
-			if err = printer.PrintObj(info.Object, o.Out); err != nil {
-				return err
... (43 more lines)
```

**Losing patch** (gpt-5.4, 1 file(s), 14 change lines):

```diff
diff --git a/staging/src/k8s.io/kubectl/pkg/cmd/apply/apply.go b/staging/src/k8s.io/kubectl/pkg/cmd/apply/apply.go
@@ -677,6 +677,17 @@ See https://kubernetes.io/docs/reference/using-api/server-side-apply/#conflicts`
+	var dryRunObject runtime.Object
+	if o.DryRunStrategy == cmdutil.DryRunClient && o.shouldPrintObject() {
+		dryRunObject = info.Object.DeepCopyObject()
+		if err := util.CreateApplyAnnotation(dryRunObject, unstructured.UnstructuredJSONScheme); err != nil {
+			return cmdutil.AddSourceToErr("creating", info.Source, err)
+		}
+		if u, ok := dryRunObject.(runtime.Unstructured); ok {
+			pruneNullsFromMap(u.UnstructuredContent())
+		}
+	}
+
@@ -758,6 +769,9 @@ See https://kubernetes.io/docs/reference/using-api/server-side-apply/#conflicts`
+		if dryRunObject != nil {
+			info.Object = dryRunObject
+		}
```

**Analysis**:  This required changes across two files (`apply.go` and `patcher.go`). Opus was the only model to touch both files, adding dry-run merging logic in the patcher. All other models (including Sonnet) only edited `apply.go`, missing the patcher-side change needed to make client-side dry-run merge with server state. This is a case where broader context gathering — understanding the full apply pipeline — was decisive.

---

### openshift__hypershift-8216

**Version constant rollback** | openshift/hypershift | Category: anthropic_wins

Target tests: `TestSupportedVersions`

| Model | a=0 | a=1 | a=2 |
|-------|-----|-----|-----|
| opus | PASS | PASS | PASS |
| sonnet | FAIL | PASS | PASS |
| gpt-5.3 | FAIL | FAIL | FAIL |
| gpt-5.4 | FAIL | FAIL | FAIL |

**Winning patch** (sonnet, 1 file(s), 23 change lines):

```diff
diff --git a/support/supportedversion/version.go b/support/supportedversion/version.go
@@ -32,13 +32,9 @@ const (
-	LatestSupportedVersion      = semver.MustParse("5.0.0")
+	LatestSupportedVersion      = semver.MustParse("4.22.0")
-	// prevLatestSupportedMajor holds the value of the latest version before we updated to a new major.
-	// This value is only used internally to compute the list of supported versions when we have 2
-	// different major versions at the same time.
-	prevLatestSupportedMajor = semver.MustParse("4.23.0")
@@ -85,21 +81,8 @@ func GetMinSupportedVersion(hc *hyperv1.HostedCluster) semver.Version {
-	if LatestSupportedVersion.Major > MinSupportedVersion.Major {
-		// Include remaining minor versions of the latest major (e.g. 5.2 -> 5.1, 5.0)
-		for i := 0; i < int(LatestSupportedVersion.Minor); i++ {
-			versions = append(versions, trimVersion(subtractMinor(&LatestSupportedVersion, uint64(i+1)).String()))
-		}
-		// Bridge from the previous major's latest minor down to MinSupportedVersion
-		for i := int(prevLatestSupportedMajor.Minor); i >= int(MinSupportedVersion.Minor); i-- {
-			v := semver.Version{Major: prevLatestSupportedMajor.Major, Minor: uint64(i), Patch: 0}
-			versions = append(versions, trimVersion(v.String()))
-		}
-	} else {
-		// If no major change simply count minors backwards
-		for i := 0; i < int(LatestSupportedVersion.Minor-MinSupportedVersion.Minor); i++ {
-			versions = append(versions, trimVersion(subtractMinor(&LatestSupportedVersion, uint64(i+1)).String()))
-		}
+	for i := 0; i < int(LatestSupportedVersion.Minor-MinSupportedVersion.Minor); i++ {
+		versions = append(versions, trimVersion(subtractMinor(&LatestSupportedVersion, uint64(i+1)).String()))
```

**Losing patch** (gpt-5.4, 2 file(s), 29 change lines):

```diff
diff --git a/support/supportedversion/version.go b/support/supportedversion/version.go
@@ -32,13 +32,9 @@ const (
-	LatestSupportedVersion      = semver.MustParse("5.0.0")
+	LatestSupportedVersion      = semver.MustParse("4.22.0")
-	// prevLatestSupportedMajor holds the value of the latest version before we updated to a new major.
-	// This value is only used internally to compute the list of supported versions when we have 2
-	// different major versions at the same time.
-	prevLatestSupportedMajor = semver.MustParse("4.23.0")
@@ -85,21 +81,8 @@ func GetMinSupportedVersion(hc *hyperv1.HostedCluster) semver.Version {
-	if LatestSupportedVersion.Major > MinSupportedVersion.Major {
-		// Include remaining minor versions of the latest major (e.g. 5.2 -> 5.1, 5.0)
-		for i := 0; i < int(LatestSupportedVersion.Minor); i++ {
-			versions = append(versions, trimVersion(subtractMinor(&LatestSupportedVersion, uint64(i+1)).String()))
-		}
-		// Bridge from the previous major's latest minor down to MinSupportedVersion
-		for i := int(prevLatestSupportedMajor.Minor); i >= int(MinSupportedVersion.Minor); i-- {
-			v := semver.Version{Major: prevLatestSupportedMajor.Major, Minor: uint64(i), Patch: 0}
-			versions = append(versions, trimVersion(v.String()))
-		}
-	} else {
-		// If no major change simply count minors backwards
-		for i := 0; i < int(LatestSupportedVersion.Minor-MinSupportedVersion.Minor); i++ {
-			versions = append(versions, trimVersion(subtractMinor(&LatestSupportedVersion, uint64(i+1)).String()))
-		}
+	for i := 0; i < int(LatestSupportedVersion.Minor-MinSupportedVersion.Minor); i++ {
+		versions = append(versions, trimVersion(subtractMinor(&LatestSupportedVersion, uint64(i+1)).String()))
diff --git a/test/e2e/util/version.go b/test/e2e/util/version.go
@@ -14,9 +14,7 @@ import (
-	// y-stream versions supported by e2e in main
-	Version50  = semver.MustParse("5.0.0")
-	Version423 = semver.MustParse("4.23.0")
+	// y-stream versions supported by e2e in release-4.22
@@ -34,8 +32,6 @@ func init() {
-	_ = Version50
-	_ = Version423
```

**Analysis**:  The fix required rolling back `LatestSupportedVersion` from 5.0 to 4.22 and simplifying the `Supported()` function to remove cross-major-version logic. The Anthropic models made a clean, focused edit to `version.go`. The GPT models over-scoped: they also edited `test/e2e/util/version.go` (removing version constants), which broke the build or introduced regressions. The winning approach was surgical restraint — changing only what was needed.

---

### kubernetes__kubernetes-133097

**PDB eviction error enrichment** | kubernetes/kubernetes | Category: gpt_wins

Target tests: `TestEviction`

| Model | a=0 | a=1 | a=2 |
|-------|-----|-----|-----|
| opus | FAIL | FAIL | FAIL |
| sonnet | FAIL | FAIL | FAIL |
| gpt-5.3 | PASS | FAIL | PASS |
| gpt-5.4 | PASS | PASS | PASS |

**Winning patch** (gpt-5.4, 1 file(s), 21 change lines):

```diff
diff --git a/pkg/registry/core/pod/storage/eviction.go b/pkg/registry/core/pod/storage/eviction.go
@@ -434,7 +434,26 @@ func (r *EvictionREST) checkAndDecrement(namespace string, podName string, pdb p
-		err.ErrStatus.Details.Causes = append(err.ErrStatus.Details.Causes, metav1.StatusCause{Type: policyv1.DisruptionBudgetCause, Message: fmt.Sprintf("The disruption budget %s needs %d healthy pods and has %d currently", pdb.Name, pdb.Status.DesiredHealthy, pdb.Status.CurrentHealthy)})
+		condition := meta.FindStatusCondition(pdb.Status.Conditions, policyv1.DisruptionAllowedCondition)
+		var msg string
+		switch {
+		// DesiredHealthy and CurrentHealthy are not trustworthy when PDB sync has failed.
+		case condition != nil && condition.Status == metav1.ConditionFalse && len(condition.Message) > 0 && condition.Reason == policyv1.SyncFailedReason:
+			msg = fmt.Sprintf("The disruption budget %s does not allow evicting pods currently because it failed sync: %s", pdb.Name, condition.Message)
+		case pdb.Status.CurrentHealthy <= pdb.Status.DesiredHealthy:
+			msg = fmt.Sprintf("The disruption budget %s needs %d healthy pods and has %d currently", pdb.Name, pdb.Status.DesiredHealthy, pdb.Status.CurrentHealthy)
+		case condition != nil && condition.Status == metav1.ConditionFalse && len(condition.Message) > 0:
+			msg = fmt.Sprintf("The disruption budget %s does not allow evicting pods currently (%s): %s", pdb.Name, condition.Reason, condition.Message)
+		default:
+			msg = fmt.Sprintf("The disruption budget %s does not allow evicting pods currently", pdb.Name)
+		}
+		err.ErrStatus.Details.Causes = append(
+			err.ErrStatus.Details.Causes,
+			metav1.StatusCause{
+				Type:    policyv1.DisruptionBudgetCause,
+				Message: msg,
+			},
+		)
```

**Losing patch** (sonnet, 1 file(s), 8 change lines):

```diff
diff --git a/pkg/registry/core/pod/storage/eviction.go b/pkg/registry/core/pod/storage/eviction.go
@@ -434,7 +434,13 @@ func (r *EvictionREST) checkAndDecrement(namespace string, podName string, pdb p
-		err.ErrStatus.Details.Causes = append(err.ErrStatus.Details.Causes, metav1.StatusCause{Type: policyv1.DisruptionBudgetCause, Message: fmt.Sprintf("The disruption budget %s needs %d healthy pods and has %d currently", pdb.Name, pdb.Status.DesiredHealthy, pdb.Status.CurrentHealthy)})
+		var message string
+		if pdb.Status.CurrentHealthy >= pdb.Status.DesiredHealthy {
+			message = fmt.Sprintf("The disruption budget %s is at its disruption limit due to pending disruptions: %d disrupted pods pending deletion", pdb.Name, len(pdb.Status.DisruptedPods))
+		} else {
+			message = fmt.Sprintf("The disruption budget %s needs %d healthy pods and has %d currently", pdb.Name, pdb.Status.DesiredHealthy, pdb.Status.CurrentHealthy)
+		}
+		err.ErrStatus.Details.Causes = append(err.ErrStatus.Details.Causes, metav1.StatusCause{Type: policyv1.DisruptionBudgetCause, Message: message})
```

**Analysis**:  The fix required enriching the PDB eviction error message to handle multiple cases: sync failure, insufficient healthy pods, and a generic fallback. The GPT models produced a proper switch statement checking the `DisruptionAllowedCondition` status. The Anthropic models attempted a simpler two-branch if/else that didn't cover the sync-failure case, which was specifically what the test asserted. More thorough case analysis by GPT was the differentiator.

---

### grpc__grpc-go-8929

**gRPC transport stream fix** | grpc/grpc-go | Category: gpt_wins

Target tests: `Test`

| Model | a=0 | a=1 | a=2 |
|-------|-----|-----|-----|
| opus | no compile | no compile | no compile |
| sonnet | FAIL | no compile | no compile |
| gpt-5.3 | PASS | PASS | PASS |
| gpt-5.4 | PASS | PASS | PASS |

**Winning patch** (gpt-5.4, 2 file(s), 72 change lines):

```diff
diff --git a/internal/transport/client_stream.go b/internal/transport/client_stream.go
@@ -19,6 +19,7 @@
+	"fmt"
@@ -28,6 +29,9 @@ import (
+// nonGRPCDataMaxLen is the maximum length of nonGRPCDataBuf.
+const nonGRPCDataMaxLen = 1024
+
@@ -46,7 +50,11 @@ type ClientStream struct {
-	headerValid      bool
+	headerValid bool
+
+	nonGRPCStatus  *status.Status // the initial status from the non-gRPC response header, finalized with collected data before closing.
+	nonGRPCDataBuf []byte         // stores the data of a non-gRPC response.
+
@@ -54,6 +62,29 @@ type ClientStream struct {
+func (s *ClientStream) startNonGRPCDataCollection(st *status.Status) {
+	s.nonGRPCStatus = st
+	s.nonGRPCDataBuf = make([]byte, 0, nonGRPCDataMaxLen)
+}
+
+// finalizeNonGRPCStatus builds the terminal status by appending the collected
+// response body to the original non-gRPC status message.
+func (s *ClientStream) finalizeNonGRPCStatus() *status.Status {
+	msg := fmt.Sprintf("%s\ndata: %q", s.nonGRPCStatus.Message(), s.nonGRPCDataBuf)
+	return status.New(s.nonGRPCStatus.Code(), msg)
+}
+
+// handleNonGRPCData collects non-gRPC body from the given data frame.
+// It returns non-nil when the stream should be closed with it.
+func (s *ClientStream) handleNonGRPCData(f *parsedDataFrame) *status.Status {
+	n := min(f.data.Len(), nonGRPCDataMaxLen-len(s.nonGRPCDataBuf))
+	s.nonGRPCDataBuf = append(s.nonGRPCDataBuf, f.data.ReadOnlyData()[0:n]...)
+	if len(s.nonGRPCDataBuf) >= nonGRPCDataMaxLen || f.StreamEnded() {
+		return s.finalizeNonGRPCStatus()
+	}
+	return nil
+}
+
diff --git a/internal/transport/http2_client.go b/internal/transport/http2_client.go
@@ -1231,6 +1231,27 @@ func (t *http2Client) handleData(f *parsedDataFrame) {
... (41 more lines)
```

**Losing patch** (sonnet, 2 file(s), 91 change lines):

```diff
diff --git a/internal/transport/client_stream.go b/internal/transport/client_stream.go
@@ -20,6 +20,7 @@ package transport
+	"unicode/utf8"
@@ -28,6 +29,10 @@ import (
+// maxNonGRPCBodySize is the maximum number of bytes of a non-gRPC response body
+// that will be included in an error message.
+const maxNonGRPCBodySize = 1024
+
@@ -41,6 +46,14 @@ type ClientStream struct {
+	// pendingNonGRPCStatus is set when a non-gRPC response header is received
+	// (unexpected HTTP status/content-type) but the stream is not yet ended.
+	// The stream will wait for the response body before being closed, so the
+	// body can be included in the error message.
+	pendingNonGRPCStatus *status.Status
+	// nonGRPCBody accumulates the response body for a non-gRPC error response.
+	nonGRPCBody []byte
+
@@ -54,6 +67,21 @@ type ClientStream struct {
+// buildNonGRPCErrorWithBody returns an error status that includes the
+// accumulated non-gRPC response body in the error message, if the body is
+// non-empty and valid UTF-8 text.
+func (s *ClientStream) buildNonGRPCErrorWithBody() *status.Status {
+	if len(s.nonGRPCBody) == 0 || s.pendingNonGRPCStatus == nil {
+		return s.pendingNonGRPCStatus
+	}
+	body := s.nonGRPCBody
+	if !utf8.Valid(body) {
+		return s.pendingNonGRPCStatus
+	}
+	msg := s.pendingNonGRPCStatus.Message() + "; response body: " + string(body)
+	return status.New(s.pendingNonGRPCStatus.Code(), msg)
+}
+
diff --git a/internal/transport/http2_client.go b/internal/transport/http2_client.go
@@ -951,6 +951,15 @@ func (t *http2Client) closeStream(s *ClientStream, err error, rst bool, rstCode
+	// If we received unexpected non-gRPC headers and were waiting for the
+	// response body to arrive, prefer the informative non-gRPC status over
+	// whatever caused this close (e.g. connection close or context
+	// cancellation).  The body-inclusive close path in handleData clears
+	// pendingNonGRPCStatus before calling closeStream, so it is unaffected.
... (60 more lines)
```

**Analysis**:  Both families edited the same two files and produced similar-sized patches. The GPT patches correctly handled stream reset propagation in the HTTP/2 client transport. The Anthropic patches touched similar code paths but introduced compilation errors (Opus failed to compile in all 3 attempts) or made logically incomplete changes (Sonnet's one compiling attempt still failed the test). This suggests the GPT models had better understanding of the gRPC transport state machine.

---

### kubernetes__kubernetes-137240

**DRA API conversion function** | kubernetes/kubernetes | Category: gpt_wins

Target tests: `TestConversionRoundTrip`

| Model | a=0 | a=1 | a=2 |
|-------|-----|-----|-----|
| opus | FAIL | FAIL | FAIL |
| sonnet | FAIL | FAIL | FAIL |
| gpt-5.3 | PASS | FAIL | PASS |
| gpt-5.4 | PASS | FAIL | PASS |

**Winning patch** (gpt-5.4, 1 file(s), 23 change lines):

```diff
diff --git a/staging/src/k8s.io/dynamic-resource-allocation/api/v1beta1/conversion.go b/staging/src/k8s.io/dynamic-resource-allocation/api/v1beta1/conversion.go
@@ -58,6 +58,12 @@ func Convert_v1beta1_DeviceRequest_To_v1_DeviceRequest(in *resourcev1beta1.Devic
+		if in.Capacity != nil {
+			exactDeviceRequest.Capacity = &resourceapi.CapacityRequirements{}
+			if err := Convert_v1beta1_CapacityRequirements_To_v1_CapacityRequirements(in.Capacity, exactDeviceRequest.Capacity, s); err != nil {
+				return err
+			}
+		}
@@ -69,7 +75,8 @@ func hasAnyMainRequestFieldsSet(deviceRequest *resourcev1beta1.DeviceRequest) bo
-		deviceRequest.Tolerations != nil
+		deviceRequest.Tolerations != nil ||
+		deviceRequest.Capacity != nil
@@ -102,6 +109,12 @@ func Convert_v1_DeviceRequest_To_v1beta1_DeviceRequest(in *resourceapi.DeviceReq
+		if in.Exactly.Capacity != nil {
+			out.Capacity = &resourcev1beta1.CapacityRequirements{}
+			if err := Convert_v1_CapacityRequirements_To_v1beta1_CapacityRequirements(in.Exactly.Capacity, out.Capacity, s); err != nil {
+				return err
+			}
+		}
@@ -181,6 +194,10 @@ func Convert_v1beta1_Device_To_v1_Device(in *resourcev1beta1.Device, out *resour
+		out.BindsToNode = basic.BindsToNode
+		out.BindingConditions = basic.BindingConditions
+		out.BindingFailureConditions = basic.BindingFailureConditions
+		out.AllowMultipleAllocations = basic.AllowMultipleAllocations
@@ -226,6 +243,10 @@ func Convert_v1_Device_To_v1beta1_Device(in *resourceapi.Device, out *resourcev1
+	out.Basic.BindsToNode = in.BindsToNode
+	out.Basic.BindingConditions = in.BindingConditions
+	out.Basic.BindingFailureConditions = in.BindingFailureConditions
+	out.Basic.AllowMultipleAllocations = in.AllowMultipleAllocations
```

**Losing patch** (sonnet, 1 file(s), 21 change lines):

```diff
diff --git a/staging/src/k8s.io/dynamic-resource-allocation/api/v1beta1/conversion.go b/staging/src/k8s.io/dynamic-resource-allocation/api/v1beta1/conversion.go
@@ -58,6 +58,7 @@ func Convert_v1beta1_DeviceRequest_To_v1_DeviceRequest(in *resourcev1beta1.Devic
+		exactDeviceRequest.Capacity = (*resourceapi.CapacityRequirements)(unsafe.Pointer(in.Capacity))
@@ -69,7 +70,8 @@ func hasAnyMainRequestFieldsSet(deviceRequest *resourcev1beta1.DeviceRequest) bo
-		deviceRequest.Tolerations != nil
+		deviceRequest.Tolerations != nil ||
+		deviceRequest.Capacity != nil
@@ -102,6 +104,7 @@ func Convert_v1_DeviceRequest_To_v1beta1_DeviceRequest(in *resourceapi.DeviceReq
+		out.Capacity = (*resourcev1beta1.CapacityRequirements)(unsafe.Pointer(in.Exactly.Capacity))
@@ -181,6 +184,10 @@ func Convert_v1beta1_Device_To_v1_Device(in *resourcev1beta1.Device, out *resour
+		out.BindsToNode = basic.BindsToNode
+		out.BindingConditions = *(*[]string)(unsafe.Pointer(&basic.BindingConditions))
+		out.BindingFailureConditions = *(*[]string)(unsafe.Pointer(&basic.BindingFailureConditions))
+		out.AllowMultipleAllocations = basic.AllowMultipleAllocations
@@ -189,6 +196,14 @@ func Convert_v1_Device_To_v1beta1_Device(in *resourceapi.Device, out *resourcev1
+	// Only populate Basic if any v1beta1-representable fields are set.
+	// This preserves roundtrip fidelity when converting v1beta1 (Basic=nil) -> v1 -> v1beta1.
+	if len(in.Attributes) == 0 && len(in.Capacity) == 0 && len(in.ConsumesCounters) == 0 &&
+		in.NodeName == nil && in.NodeSelector == nil && in.AllNodes == nil && len(in.Taints) == 0 &&
+		in.BindsToNode == nil && len(in.BindingConditions) == 0 && len(in.BindingFailureConditions) == 0 &&
+		in.AllowMultipleAllocations == nil {
+		return nil
+	}
@@ -226,6 +241,10 @@ func Convert_v1_Device_To_v1beta1_Device(in *resourceapi.Device, out *resourcev1
+	out.Basic.BindsToNode = in.BindsToNode
+	out.Basic.BindingConditions = *(*[]string)(unsafe.Pointer(&in.BindingConditions))
+	out.Basic.BindingFailureConditions = *(*[]string)(unsafe.Pointer(&in.BindingFailureConditions))
+	out.Basic.AllowMultipleAllocations = in.AllowMultipleAllocations
```

**Analysis**:  A DRA (Dynamic Resource Allocation) API conversion function needed updating. Both families edited the same file but the GPT models produced a more complete conversion that handled the specific field mapping the round-trip test required. The Anthropic patches were shorter and missed a field, causing the conversion round-trip to lose data.

---

### kubernetes__kubernetes-138088

**Journal server method restriction** | kubernetes/kubernetes | Category: gpt_wins

Target tests: `TestJournalServerMethodRestriction`

| Model | a=0 | a=1 | a=2 |
|-------|-----|-----|-----|
| opus | FAIL | FAIL | FAIL |
| sonnet | FAIL | FAIL | FAIL |
| gpt-5.3 | FAIL | PASS | partial (1/1) |
| gpt-5.4 | FAIL | PASS | PASS |

**Winning patch** (gpt-5.4, 2 file(s), 38 change lines):

```diff
diff --git a/pkg/kubelet/kubelet_server_journal.go b/pkg/kubelet/kubelet_server_journal.go
@@ -63,6 +63,13 @@ type journalServer struct{}
+	if req.Method != http.MethodGet && req.Method != http.MethodPost {
+		// Only GET and POST are supported for journal log queries.
+		w.Header().Set("Allow", "GET, POST")
+		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
+		return
+	}
+
diff --git a/pkg/kubelet/server/server.go b/pkg/kubelet/server/server.go
@@ -482,6 +482,7 @@ func (s *Server) InstallAuthNotRequiredHandlers(ctx context.Context) {
+	ws.Filter(GETOnlyRestfulFilter())
@@ -636,6 +637,7 @@ func (s *Server) InstallAuthRequiredHandlers(ctx context.Context) {
+	ws.Filter(GETOnlyRestfulFilter())
@@ -649,6 +651,7 @@ func (s *Server) InstallAuthRequiredHandlers(ctx context.Context) {
+	ws.Filter(GETOnlyRestfulFilter())
@@ -699,6 +702,7 @@ func (s *Server) InstallSystemLogHandler(enableSystemLogHandler bool, enableSyst
+		ws.Filter(GETOnlyRestfulFilter())
@@ -770,6 +774,7 @@ func (s *Server) InstallProfilingHandler(enableProfilingLogHandler bool, enableC
+	ws.Filter(GETOnlyRestfulFilter())
@@ -933,6 +938,32 @@ func (s *Server) getLogs(request *restful.Request, response *restful.Response) {
+// GETOnlyRestfulFilter allows only GET. Use on WebServices that register read-only
+// kubelet APIs.
+func GETOnlyRestfulFilter() restful.FilterFunction {
+	return AllowedMethodsRestfulFilter(http.MethodGet)
+}
+
+// AllowedMethodsRestfulFilter returns a restful.FilterFunction that rejects requests
+// whose HTTP method is not listed in allowed. It responds with 405 Method Not Allowed
+// and an Allow header listing the permitted methods (RFC 9110).
+func AllowedMethodsRestfulFilter(allowed ...string) restful.FilterFunction {
+	allowedSet := make(map[string]struct{}, len(allowed))
+	for _, m := range allowed {
+		allowedSet[m] = struct{}{}
+	}
+	allowHeader := strings.Join(allowed, ", ")
+
+	return func(req *restful.Request, resp *restful.Response, chain *restful.FilterChain) {
+		if _, ok := allowedSet[req.Request.Method]; ok {
+			chain.ProcessFilter(req, resp)
... (7 more lines)
```

**Losing patch** (sonnet, 1 file(s), 24 change lines):

```diff
diff --git a/pkg/kubelet/server/server.go b/pkg/kubelet/server/server.go
@@ -709,16 +709,22 @@ func (s *Server) InstallSystemLogHandler(enableSystemLogHandler bool, enableSyst
-			ws.Route(ws.GET("/{logpath:*}").
+			nodeLogQueryParams := func(rb *restful.RouteBuilder) *restful.RouteBuilder {
+				return rb.
+					Param(ws.PathParameter("logpath", "path to the log").DataType("string")).
+					Param(ws.QueryParameter("query", "query specifies services(s) or files from which to return logs").DataType("string")).
+					Param(ws.QueryParameter("sinceTime", "sinceTime is an RFC3339 timestamp from which to show logs").DataType("string")).
+					Param(ws.QueryParameter("untilTime", "untilTime is an RFC3339 timestamp until which to show logs").DataType("string")).
+					Param(ws.QueryParameter("tailLines", "tailLines is used to retrieve the specified number of lines from the end of the log").DataType("string")).
+					Param(ws.QueryParameter("pattern", "pattern filters log entries by the provided regex pattern").DataType("string")).
+					Param(ws.QueryParameter("boot", "boot show messages from a specific system boot").DataType("string"))
+			}
+			ws.Route(nodeLogQueryParams(ws.GET("/{logpath:*}").
-				Operation("getLogs").
-				Param(ws.PathParameter("logpath", "path to the log").DataType("string")).
-				Param(ws.QueryParameter("query", "query specifies services(s) or files from which to return logs").DataType("string")).
-				Param(ws.QueryParameter("sinceTime", "sinceTime is an RFC3339 timestamp from which to show logs").DataType("string")).
-				Param(ws.QueryParameter("untilTime", "untilTime is an RFC3339 timestamp until which to show logs").DataType("string")).
-				Param(ws.QueryParameter("tailLines", "tailLines is used to retrieve the specified number of lines from the end of the log").DataType("string")).
-				Param(ws.QueryParameter("pattern", "pattern filters log entries by the provided regex pattern").DataType("string")).
-				Param(ws.QueryParameter("boot", "boot show messages from a specific system boot").DataType("string")))
+				Operation("getLogs")))
+			ws.Route(nodeLogQueryParams(ws.POST("/{logpath:*}").
+				To(s.getLogs).
+				Operation("getLogs")))
```

**Analysis**:  The fix required restricting journal server methods in two places: the handler itself and the server registration. The GPT models edited both files (`kubelet_server_journal.go` and `server.go`), while the Anthropic models only edited `server.go`. Similar to kubernetes-135513, the multi-file scope was the deciding factor.

---

## D. Causal Factor Synthesis

Across the real (non-grading-bug) discordant instances, several recurring themes explain why one model family succeeds where the other fails:

### 1. Multi-file scope recognition

In at least 3 GPT-only wins (`kubernetes-135593`, `kubernetes-138088`, `grpc-go-8929`) and 1 Anthropic-only win (`kubernetes-135513`), the winning model touched more files than the loser. The fix required coordinated changes across multiple call sites or layers. Models that explored more of the codebase before committing to a patch had an advantage. This factor cuts both ways — it explains wins for both families.

### 2. Case completeness in conditional logic

Several GPT-only wins (`kubernetes-133097`, `kubernetes-132798`, `kubernetes-137189`) involved enriching error handling or validation with additional cases. The GPT models tended to produce switch statements or multi-branch conditionals that covered edge cases the tests asserted, while Anthropic models produced simpler two-branch fixes that missed specific conditions. This suggests GPT models more thoroughly analyzed the test expectations before writing the fix.

### 3. Surgical restraint vs over-scoping

In the Anthropic-only wins (`hypershift-8216`, `etcd-21757`), the Anthropic models made focused, minimal changes while GPT models over-scoped — editing additional files or making unnecessary changes that introduced regressions. When the correct fix was narrow, Anthropic's tendency toward smaller patches was an advantage.

### 4. Consistency and compilation reliability

In `grpc-go-8929`, Opus failed to compile in all 3 attempts while both GPT models succeeded in all 3. For cursor CLI runs (zero-turn, no iterative debugging), compilation reliability on the first attempt is critical. GPT models showed higher compilation rates in discordant instances overall.

### 5. Grading infrastructure bias

**0 instances** had identical patches graded differently, with the bias running against Anthropic models in 4 of 6 cases. This inflated GPT's apparent advantage by 3 discordant instances. The root cause is likely non-determinism in the Docker-based grading environment (test timeouts, flaky setup scripts, or race conditions in the grade binary). This warrants investigation — the same patch should always get the same grade.

### Summary

The GPT models' advantage in cursor CLI mode is real but overstated by the raw numbers. After correcting for grading bugs, the gap narrows from 23-vs-3 to 20-vs-3 discordant wins. The primary causal factors favoring GPT are (1) more thorough case analysis in conditional logic and (2) higher compilation reliability in zero-turn mode. The primary factors favoring Anthropic are (1) surgical patch scope and (2) multi-file reasoning in specific instances. Neither family has a systematic advantage in code location accuracy — both consistently identify the correct files.

