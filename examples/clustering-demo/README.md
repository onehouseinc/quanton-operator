# Quanton Clustering Demo

End-to-end clustering demos for Apache Hudi and Apache Iceberg on minikube,
exercising Quanton's native clustering acceleration via the
`spark.quanton.clustering.accelerate=true` feature flag.

Each demo writes 100 rows with a complex schema (`Struct` / `Array` / `Map` /
nested struct-of-array), forces a many-tiny-files layout, then runs the
format's native clustering procedure with sort-by columns.

| Format | Manifest | Script | Clustering procedure |
|---|---|---|---|
| Hudi | [`quanton-hudi-clustering-demo.yaml`](quanton-hudi-clustering-demo.yaml) | [`hudi_clustering_demo.py`](hudi_clustering_demo.py) | `CALL run_clustering(order='region,ts', op='scheduleandexecute')` |
| Iceberg | [`quanton-iceberg-clustering-demo.yaml`](quanton-iceberg-clustering-demo.yaml) | [`iceberg_clustering_demo.py`](iceberg_clustering_demo.py) | `CALL rewrite_data_files(strategy='sort', sort_order='region ASC, ts ASC')` |

## Prerequisites

- minikube running with at least 4 CPUs / 8 GB RAM
- Spark Operator and Quanton Operator already installed and `Running`
  (see [`../../README.md`](../../README.md))

## Run

### Hudi

```bash
kubectl apply -f quanton-hudi-clustering-demo.yaml
kubectl logs -f quanton-hudi-clustering-demo-driver -n default
```

Expected tail:

```
[hudi-clustering] On-disk parquet files: 50 -> 51 (Hudi keeps old files; new ones + .replacecommit are added)
[hudi-clustering] .replacecommit files in .hoodie/: 1
[hudi-clustering] PASS — 100 rows preserved, 1 replacecommit(s) on timeline
```

### Iceberg

```bash
kubectl apply -f quanton-iceberg-clustering-demo.yaml
kubectl logs -f quanton-iceberg-clustering-demo-driver -n default
```

Expected tail:

```
[iceberg-clustering] Files: 40 -> 1
[iceberg-clustering] PASS — 100 rows preserved, files compacted 40 -> 1
```

## Files

| File | Role |
|---|---|
| `quanton-hudi-clustering-demo.yaml` | ConfigMap (inline `hudi_clustering_demo.py`) + 2 Gi PVC + QuantonSparkApplication. Sets `spark.quanton.clustering.accelerate: "true"`. |
| `quanton-iceberg-clustering-demo.yaml` | Same shape for Iceberg. Adds `spark.{driver,executor}.extraClassPath` pointing at the iceberg jars bundled in the Quanton image (see notes below). |
| `hudi_clustering_demo.py` | Standalone source — generates 100 complex-schema rows, writes a Hudi COW table with `hoodie.parquet.max.file.size=1024` (1 KiB) to force many tiny files, calls `run_clustering`, asserts a `.replacecommit` appears on the timeline. |
| `iceberg_clustering_demo.py` | Standalone source — creates a Hadoop-catalog Iceberg table, writes 20 sequential 5-row batches (→ ~40 small data files), calls `rewrite_data_files(strategy='sort')`, asserts file count decreased. |

## Cleanup

```bash
kubectl delete -f quanton-hudi-clustering-demo.yaml
kubectl delete -f quanton-iceberg-clustering-demo.yaml
kubectl delete pvc quanton-hudi-clustering-demo-pvc quanton-iceberg-clustering-demo-pvc -n default
```

## Notes

- **Apple Silicon (M1/M2/M3):** older Quanton spark images
  (`release-v0.2.0-al2023` and earlier) only shipped a Graviton SVE2 build of
  the native engine and `SIGILL` on aarch64 Mac. `release-v0.9.0-al2023` and
  later include an aarch64 build that works on Apple Silicon.

- **Iceberg jars on the Quanton image:** the Quanton spark image bundles
  `iceberg-spark-runtime.jar`, `iceberg-aws-bundle.jar`, and
  `iceberg-spark-quanton.jar` at `/opt/spark/user-jars/`. The Iceberg demo
  manifest puts them on the classpath via `spark.{driver,executor}.extraClassPath`
  rather than `spark.jars.packages` — pulling iceberg via maven on top of the
  bundled jars puts two copies on the classpath with mismatched shaded-parquet
  classes and breaks with a `ClassCastException` between
  `org.apache.parquet.schema.MessageType` and the iceberg-shaded equivalent.

- **Hudi clustering file count is not a reliable success signal:** Hudi
  tombstones old files via the `.hoodie/` timeline (a new `.replacecommit`)
  rather than deleting them on disk. The Hudi demo's assertion is on
  `.replacecommit` count, not on parquet-file count.

- **Trigger this demo via Claude Code:** run `/run-clustering` — see
  [`../../.claude/skills/run-clustering/SKILL.md`](../../.claude/skills/run-clustering/SKILL.md).
