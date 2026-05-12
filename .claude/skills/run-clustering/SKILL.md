---
name: run-clustering
description: Run the Hudi or Iceberg clustering demo on minikube — writes a 100-row table with a complex (Struct/Array/Map/nested) schema, forces a many-tiny-files layout, then triggers the format's native clustering procedure with spark.quanton.clustering.accelerate=true and verifies it succeeded
allowed-tools: Bash, Read, AskUserQuestion
---

# Run Quanton Clustering Demo: Hudi and/or Iceberg

You are a guided demo agent for Quanton's native clustering acceleration. Run the chosen demo, give live progress, and report whether clustering succeeded.

The demos live in `examples/`:

| Format | Manifest | Script |
|---|---|---|
| Hudi | `examples/clustering-demo/quanton-hudi-clustering-demo.yaml` | `examples/clustering-demo/hudi_clustering_demo.py` |
| Iceberg | `examples/clustering-demo/quanton-iceberg-clustering-demo.yaml` | `examples/clustering-demo/iceberg_clustering_demo.py` |

Each manifest is a self-contained ConfigMap (inline script) + PVC + QuantonSparkApplication. Both write 100 rows with a complex schema, force a many-tiny-files layout, then call the native clustering procedure with `spark.quanton.clustering.accelerate=true`.

## Phase 0: Interactive Configuration

Use AskUserQuestion. Keep it short — the demo is small.

### Q1: Which format?

> "Which clustering demo should I run?"

Options:
- **Hudi only** — `run_clustering(order='region,ts', op='scheduleandexecute')`
- **Iceberg only** — `rewrite_data_files(strategy='sort', sort_order='region ASC, ts ASC')`
- **Both** — Hudi then Iceberg, sequentially (PVCs are independent so they don't conflict, but pods are scheduled serially for clarity)

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
kubectl delete -f examples/clustering-demo/quanton-<fmt>-clustering-demo.yaml --ignore-not-found
kubectl delete pvc quanton-<fmt>-clustering-demo-pvc -n default --ignore-not-found
```

### 2. Apply the manifest

```bash
kubectl apply -f examples/clustering-demo/quanton-<fmt>-clustering-demo.yaml
```

Tell the user: "Submitted `quanton-<fmt>-clustering-demo`. The Quanton operator will provision the driver + executor pods. The first run pulls the Quanton spark image (~3.5 GB on minikube) and, for Hudi, downloads `hudi-spark3.5-bundle:0.15.0` from Maven Central — expect a 1–3 min cold start."

### 3. Live progress

Poll every ~20s until the QuantonSparkApplication phase is `COMPLETED` or `FAILED`:

```bash
kubectl get quantonsparkapplication quanton-<fmt>-clustering-demo -n default \
  -o jsonpath='{.status.phase}'
```

Between polls, give the user a one-line status update:
- **UNKNOWN**: "Operator reconciling, driver not yet scheduled..."
- **RUNNING**: "Driver pod running — generating data and running clustering."
- **COMPLETED** / **FAILED**: stop polling, move to step 4.

If the user asks for more detail mid-flight, tail the driver log:
```bash
kubectl logs -n default quanton-<fmt>-clustering-demo-driver --tail=20
```

### 4. Report the outcome

After the CRD reaches a terminal phase, grep the driver log for the demo's own status lines:

```bash
kubectl logs -n default quanton-<fmt>-clustering-demo-driver 2>&1 \
  | grep -E "<fmt>-clustering|PASS|FAIL"
```

The demos print clear markers:

- **Hudi** `PASS` looks like:
  ```
  [hudi-clustering] On-disk parquet files: 50 -> 51 (Hudi keeps old files; new ones + .replacecommit are added)
  [hudi-clustering] .replacecommit files in .hoodie/: 1
  [hudi-clustering] PASS — 100 rows preserved, 1 replacecommit(s) on timeline
  ```
- **Iceberg** `PASS` looks like:
  ```
  [iceberg-clustering] Files: 40 -> 1
  [iceberg-clustering] PASS — 100 rows preserved, files compacted 40 -> 1
  ```

Surface those lines verbatim to the user and call out the file-count change. **Do not present wall-times** — the demos use tiny 100-row tables where startup dominates and Hudi vs Iceberg timings are not comparable; surfacing them invites misleading conclusions. If the demo failed, also tail the last 50 log lines and surface any `Exception` / `Caused by` lines.

### 5. (Optional) Native-acceleration markers

If the user asks "did Quanton's native path actually engage?" — grep the driver log:

```bash
kubectl logs -n default quanton-<fmt>-clustering-demo-driver 2>&1 \
  | grep -iE "NativeClusteringGroupWriter|libvelox.so|VeloxBackend|Components registered"
```

Look for:
- `Loaded clustering group writer: quanton-velox-clustering-0x (ai.onehouse.hudi.clustering.NativeClusteringGroupWriterImpl)` — Hudi native writer engaged
- `libvelox.so has been loaded` / `Components registered within order: velox, velox-hudi, velox-delta, velox-iceberg` — Quanton's native engine up
  (the literal strings come from the runtime; they're the markers to grep for)

Note: `acceleratedStages: 0` from `QuantonAccelerationTracker` does **not** mean acceleration is off — the tracker only counts natively-accelerated *Spark query stages*, not the Hudi/Iceberg clustering procedure code paths. The native writer can be active even when this counter is 0.

## Phase 2: Cleanup (optional)

Ask the user: "Demo finished. Should I clean up the resources?"

- Options: "Clean up everything" / "Keep them for inspection"

If clean up:
```bash
kubectl delete -f examples/clustering-demo/quanton-<fmt>-clustering-demo.yaml --ignore-not-found
kubectl delete pvc quanton-<fmt>-clustering-demo-pvc -n default --ignore-not-found
```

(The PVC is named `quanton-<fmt>-clustering-demo-pvc`; it's gitignored from the chart and safe to delete.)

## Caveats

- **Apple Silicon (M1/M2/M3):** Older Quanton spark images (`release-v0.2.0-al2023` and earlier) only ship a Graviton SVE2 build of the native engine and will `SIGILL` on aarch64 Mac. The `release-v0.9.0-al2023` and later images include an aarch64 build that works on Apple Silicon.
- **Hudi clustering file count:** Hudi tombstones old files via the `.hoodie` timeline (`.replacecommit`) instead of physically deleting them. The Hudi demo asserts on the timeline, not on the parquet-file count.
- **Iceberg jars on the Quanton image:** Bundled at `/opt/spark/user-jars/`. The manifest uses `spark.{driver,executor}.extraClassPath` to put them on the classpath; do NOT add iceberg via `spark.jars.packages` in parallel — a second shaded-parquet copy on the classpath breaks with a `ClassCastException`.
