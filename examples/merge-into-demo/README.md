# Quanton MERGE INTO Demo

End-to-end `MERGE INTO` demos for Apache Hudi and Apache Iceberg, runnable on minikube via
QuantonSparkApplication.

Each demo creates a `customers` table, inserts 10 rows, then runs `MERGE INTO ... USING source ...`
with 3 updates and 3 inserts. Verifies the final state (`10 -> 13 rows, 3 'vip'`).

| Format  | Manifest                                                                     | Script                                                          |
|---------|------------------------------------------------------------------------------|-----------------------------------------------------------------|
| Hudi    | [`quanton-hudi-merge-into-demo.yaml`](quanton-hudi-merge-into-demo.yaml)     | [`hudi_merge_into_demo.py`](hudi_merge_into_demo.py)            |
| Iceberg | [`quanton-iceberg-merge-into-demo.yaml`](quanton-iceberg-merge-into-demo.yaml) | [`iceberg_merge_into_demo.py`](iceberg_merge_into_demo.py)      |

## Prerequisites

- minikube running with at least 4 CPUs / 8 GB RAM
- Spark Operator and Quanton Operator installed and `Running`
  (see [`../../README.md`](../../README.md))

## Run

### Hudi

```bash
kubectl apply -f quanton-hudi-merge-into-demo.yaml
kubectl logs -f quanton-hudi-merge-into-demo-driver -n default
```

Expected tail:

```
[hudi-merge] After MERGE: 13 rows, 3 'vip'
[hudi-merge] PASS — 10 -> 13 rows, 3 updated to 'vip'
```

### Iceberg

```bash
kubectl apply -f quanton-iceberg-merge-into-demo.yaml
kubectl logs -f quanton-iceberg-merge-into-demo-driver -n default
```

Expected tail:

```
[iceberg-merge] After MERGE: 13 rows, 3 'vip'
[iceberg-merge] PASS — 10 -> 13 rows, 3 updated to 'vip'
```

## Cleanup

```bash
kubectl delete -f quanton-hudi-merge-into-demo.yaml
kubectl delete -f quanton-iceberg-merge-into-demo.yaml
kubectl delete pvc quanton-hudi-merge-into-demo-pvc quanton-iceberg-merge-into-demo-pvc -n default
```

## Files

| File                                     | Role                                                                                                                                          |
|------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| `quanton-hudi-merge-into-demo.yaml`      | ConfigMap (inline `hudi_merge_into_demo.py`) + 2 Gi PVC + QuantonSparkApplication. Hudi COW table; pulls `hudi-spark3.5-bundle` from Maven.   |
| `quanton-iceberg-merge-into-demo.yaml`   | Same shape for Iceberg. Uses Hadoop catalog on the PVC; iceberg jars are pre-bundled in the Quanton image at `/opt/spark/user-jars/`.         |
| `hudi_merge_into_demo.py`                | Standalone source — CREATE Hudi COW table, INSERT 10 rows, MERGE INTO with 3 updates + 3 inserts, assert `10 -> 13` rows and 3 `'vip'`.       |
| `iceberg_merge_into_demo.py`             | Standalone source — same workflow for Iceberg (Hadoop catalog).                                                                               |
