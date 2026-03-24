# TPC-DS Benchmark

Compares OSS Apache Spark vs Quanton on Kubernetes using the TPC-DS read benchmark — 99 TPC-DS queries on Parquet.

## Overview

1. **Generates TPC-DS data** (default SF=1 / 1GB) using `dsdgen` into Parquet format
2. **Runs 99 TPC-DS queries** on OSS Spark (Parquet) as baseline
3. **Runs 99 TPC-DS queries** on Quanton (Parquet) for comparison
4. **Prints a comparison table** with per-query timings and speedups

## Prerequisites

- minikube (4+ CPUs, 8+ GB memory, 50+ GB disk)
- kubectl
- Spark Operator installed (Kubeflow)
- Quanton Operator installed (see `docs/getting-started.md`)
- Docker CLI (uses minikube's Docker daemon)

## Quick Start

```bash
# Start minikube (if not already running)
minikube start --cpus 4 --memory 8g --disk-size 50g

# Install Spark Operator + Quanton Operator per docs/getting-started.md

# Run TPC-DS benchmark
./benchmarks/run.sh
```

## Options

```bash
# Custom scale factor (default: SF=1)
./benchmarks/run.sh --scale-factor 10

# Custom timeout per job (default: 7200s)
./benchmarks/run.sh --timeout 3600
```

## Directory Structure

```
benchmarks/
├── README.md                          # This file
├── run.sh                             # TPC-DS benchmark orchestration
├── Dockerfile.datagen                 # Spark + dsdgen image
├── scripts/
│   ├── datagen.py                     # Generate TPC-DS Parquet via dsdgen
│   └── run_queries.py                 # Execute SQL files with timing
├── sql/
│   ├── tpcds/                         # 99 TPC-DS queries (q1.sql ... q99.sql)
│   └── ddl/
│       └── create_parquet_tables.sql  # 25 CREATE TABLE USING PARQUET
├── k8s/
│   ├── pvc.yaml                       # 50Gi PVC
│   ├── datagen-job.yaml               # SparkApplication: TPC-DS data generation
│   ├── oss-spark-tpcds.yaml           # SparkApplication: OSS Spark TPC-DS
│   └── quanton-tpcds-parquet.yaml     # QuantonSparkApplication: Quanton TPC-DS
└── results/                           # Output from each run (JSON, logs)
```

## Execution Phases

| Phase | Description | K8s Resource |
|-------|-------------|--------------|
| 0 | Build datagen Docker image | Docker build |
| 1 | Create PVC + ConfigMaps | kubectl apply/create |
| 2 | Generate TPC-DS Parquet data | SparkApplication |
| 3 | OSS Spark TPC-DS on Parquet | SparkApplication |
| 4 | Quanton TPC-DS on Parquet | QuantonSparkApplication |
| 5 | Collect results + print table | kubectl cp |

## Benchmark Results

All results below are from runs using 3g executor memory (2 executors, 1 core each) on Mac M1 (Apple Silicon). We expect gains to climb higher with scale, on an actual production Spark environment.

Per-query times for each run:

- SF-1: [OSS Spark](data/sf1_oss_spark.md) | [Quanton](data/sf1_quanton.md)
- SF-10: [OSS Spark](data/sf10_oss_spark.md) | [Quanton](data/sf10_quanton.md)

### SF-1 (1 GB dataset)

| Metric | Value |
|--------|-------|
| OSS Spark avg total | 156.22s |
| Quanton avg total | 103.98s |
| **Avg speedup (per-query)** | **2.03x** |
| **Max speedup** | **8.72x** (q88) |
| **Total speedup** | **1.50x** |

### SF-10 (10 GB dataset)

| Metric | Value |
|--------|-------|
| OSS Spark avg total | 738.85s |
| Quanton avg total | 327.79s |
| **Avg speedup (per-query)** | **2.71x** |
| **Max speedup** | **7.62x** (q21) |
| **Total speedup** | **2.25x** |

## Resource Configuration

- **PVC**: 50Gi `ReadWriteOnce` — stores Parquet data and result files
- **Driver**: 2 cores, 4GB memory
- **Executors**: 2 instances, 1 core, 3GB memory
- **Service Account**: `spark-operator-spark`

## Notes

- All jobs run sequentially (PVC is `ReadWriteOnce`)
- Data gen uses a custom Docker image built in minikube's Docker daemon (`imagePullPolicy: Never`)
- Benchmark is purely Parquet-based reads
