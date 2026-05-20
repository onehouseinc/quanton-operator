---
name: run-merge-into
description: Run the Hudi or Iceberg MERGE INTO demo on minikube — creates a customers table, inserts 10 rows, runs MERGE INTO with 3 updates + 3 inserts, and verifies the final state (10 -> 13 rows, 3 'vip')
allowed-tools: Bash, Read, AskUserQuestion
---

# Run Quanton MERGE INTO Demo: Hudi and/or Iceberg

You are a guided demo agent for the Quanton Operator's MERGE INTO demos. Run the chosen demo, give live progress, and report whether the merge succeeded.

The demos live in `examples/merge-into-demo/`:

| Format | Manifest | Script |
|---|---|---|
| Hudi | `examples/merge-into-demo/quanton-hudi-merge-into-demo.yaml` | `examples/merge-into-demo/hudi_merge_into_demo.py` |
| Iceberg | `examples/merge-into-demo/quanton-iceberg-merge-into-demo.yaml` | `examples/merge-into-demo/iceberg_merge_into_demo.py` |

Each manifest is a self-contained ConfigMap (inline script) + PVC + QuantonSparkApplication. Both create a `customers` table, insert 10 rows, then run `MERGE INTO ... USING source_updates s` with 3 updates (status -> 'vip') and 3 inserts, and assert the final state is `13 rows, 3 'vip'`.

## Phase 0: Interactive Configuration

Use AskUserQuestion. Keep it short.

### Q1: Which format?

> "Which MERGE INTO demo should I run?"

Options:
- **Hudi only** — Hudi COW table, primaryKey = `id`, preCombineField = `ts`.
- **Iceberg only** — Iceberg table, Hadoop catalog on a PVC (no Glue / S3 needed).
- **Both** — Hudi then Iceberg, sequentially. PVCs are independent so they don't conflict; pods are scheduled serially for clarity.

### Q2: Cluster check (no question — just verify)

Run:
```bash
kubectl config current-context
```
Confirm it's `minikube`. If it's any other context, stop and tell the user to `kubectl config use-context minikube` first.

Then:
```bash
helm list -A | grep -E "spark-operator|quanton-operator"
```
If either operator is missing, stop and tell the user to run `/setup-and-run-example` first.

## Phase 1: Run the demo

For each chosen format:

### 1. Clean up any prior run

```bash
kubectl delete -f examples/merge-into-demo/quanton-<fmt>-merge-into-demo.yaml --ignore-not-found
kubectl delete pvc quanton-<fmt>-merge-into-demo-pvc -n default --ignore-not-found
```

### 2. Apply the manifest

```bash
kubectl apply -f examples/merge-into-demo/quanton-<fmt>-merge-into-demo.yaml
```

Tell the user: "Submitted `quanton-<fmt>-merge-into-demo`. The Quanton operator will provision the driver + executor pods. The first run pulls the Quanton spark image (~3.5 GB on minikube) and, for Hudi, downloads `hudi-spark3.5-bundle:0.15.0` from Maven Central — expect a 1–3 min cold start."

### 3. Live progress

Poll every ~15s until the QuantonSparkApplication phase is `COMPLETED` or `FAILED`:

```bash
kubectl get quantonsparkapplication quanton-<fmt>-merge-into-demo -n default \
  -o jsonpath='{.status.phase}'
```

Between polls, give the user a one-line status update:
- **UNKNOWN** / **SUBMITTED**: "Operator reconciling, driver not yet scheduled..."
- **RUNNING**: "Driver pod running — creating table, inserting rows, running MERGE INTO."
- **COMPLETED** / **FAILED**: stop polling, move to step 4.

If the user asks for more detail mid-flight, tail the driver log:
```bash
kubectl logs -n default quanton-<fmt>-merge-into-demo-driver --tail=20
```

### 4. Report the outcome

After the CRD reaches a terminal phase, grep the driver log for the demo's own status lines:

```bash
kubectl logs -n default quanton-<fmt>-merge-into-demo-driver 2>&1 \
  | grep -E "<fmt>-merge|PASS|FAIL"
```

The demos print clear markers. Successful runs end with:

- **Hudi**:
  ```
  [hudi-merge] After MERGE: 13 rows, 3 'vip'
  [hudi-merge] PASS — 10 -> 13 rows, 3 updated to 'vip'
  ```
- **Iceberg**:
  ```
  [iceberg-merge] After MERGE: 13 rows, 3 'vip'
  [iceberg-merge] PASS — 10 -> 13 rows, 3 updated to 'vip'
  ```

Surface those lines verbatim to the user and call out the `10 -> 13 rows` transition.
If the demo failed, tail the last 50 log lines and surface any `Exception` / `Caused by` lines.

## Phase 2: Cleanup (optional)

Ask the user: "Demo finished. Should I clean up the resources?"

Options: "Clean up everything" / "Keep them for inspection"

If clean up:
```bash
kubectl delete -f examples/merge-into-demo/quanton-<fmt>-merge-into-demo.yaml --ignore-not-found
kubectl delete pvc quanton-<fmt>-merge-into-demo-pvc -n default --ignore-not-found
```

## Caveats

- **Apple Silicon (M1/M2/M3):** Older Quanton spark images (`release-v0.2.0-al2023` and earlier) only ship a Graviton SVE2 build of the native engine and will `SIGILL` on aarch64 Mac. The `release-v0.9.0-al2023` and later images include an aarch64 build that works on Apple Silicon.
- **Iceberg jars on the Quanton image:** Bundled at `/opt/spark/user-jars/`. The manifest puts them on the classpath via `spark.{driver,executor}.extraClassPath`; do NOT add iceberg via `spark.jars.packages` in parallel — a second shaded-parquet copy on the classpath breaks with a `ClassCastException`.
- **Hudi cold start:** The Hudi manifest pulls `org.apache.hudi:hudi-spark3.5-bundle_2.12:0.15.0` from Maven Central on first run. Subsequent runs reuse the cached jar via `spark.jars.ivy=/tmp/ivy`.
