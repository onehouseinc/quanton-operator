# Troubleshooting

Failures that show up across the demos, the benchmark, and the operator install. Each entry
names the symptom, the command that produces the evidence, what the evidence means, and the
category. The category matters when you report: an environment problem is not an engine
problem, and the fix is different.

The skills in `.agents/skills/` point here instead of repeating these entries. Skill-specific
failures stay in the skill.

## Pods and scheduling

### Driver stays `Pending`

Run `kubectl describe pod <driver-pod> -n <namespace> | tail -20` and read the `Events`.

| Event text | Meaning | Category |
|---|---|---|
| `Insufficient cpu` or `Insufficient memory` | The executor shape or the minikube size is too small for the manifest. | Environment |
| `ImagePullBackOff` or `ErrImagePull` | The node cannot pull the image. Registry credentials or network. The first pull of the Quanton Spark image is about 3.5 GB and can take minutes on its own. | Environment |
| `ErrImageNeverPull` | The manifest says `imagePullPolicy: Never` and the image was built against a different Docker daemon. For the benchmark datagen image, rebuild after `eval "$(minikube docker-env)"`. | Environment |
| `Multi-Attach error` or a volume still attached | A `ReadWriteOnce` PVC is still bound to a pod from a previous run. Resolves itself once that pod is gone. | Environment |
| No events, pod stuck before `Running` for more than 3 minutes on a Hudi demo | The driver downloads `hudi-spark3.5-bundle_2.12:0.15.0` from Maven Central on first run. | Environment (network) |

### Pod evicted with `Evicted pod: Underutilized`

Karpenter consolidated the node. Not a minikube case. Add
`spark.kubernetes.driver.annotation.karpenter.sh/do-not-disrupt: "true"` to `sparkConf`.
Category: environment.

## Native engine

### `SIGILL` or `signal 4` in a driver or executor log

The native engine in the image does not match the CPU. Collect two facts:

```bash
uname -m
kubectl get pod <pod> -n <namespace> -o jsonpath='{.spec.containers[0].image}{"\n"}'
```

Images at `release-v0.9.0-al2023` or later carry an aarch64 build that runs on Apple Silicon.
Earlier images carry only a Graviton (SVE2) build. Report both facts and let the user pick an
image. Category: image and hardware match, not a Quanton logic problem.

### `ClassCastException` mentioning parquet on an Iceberg job

Two copies of Iceberg are on the classpath. The image bundles Iceberg at
`/opt/spark/user-jars/` and the checked-in manifests reach it through `extraClassPath`. The
usual cause is a `spark.jars.packages` entry that pulls a second copy. Check the manifest for
it. Category: configuration.

### `No object store provider found for scheme: 's3a'` from Lance

Lance uses its own object-store layer and does not know the `s3a` scheme. Use `s3://` paths
and set `fs.s3.impl=org.apache.hadoop.fs.s3a.S3AFileSystem`. Category: configuration.

## Jobs

### Phase `Failed` with no marker lines in the log

Run `kubectl logs <driver-pod> -n <namespace> --tail=80` and quote the first `Exception` or
`Caused by` line. Then run `kubectl describe <kind> <name> -n <namespace> | tail -20` for the
operator's view. An image pull error or an OOM kill is an environment problem. A traceback
inside the job's own script is a job problem. Say which one the evidence shows.

### Hudi file count did not drop after clustering

Expected. Hudi tombstones old files through the `.hoodie` timeline with a `.replacecommit`
instead of deleting them. The clustering demo's verdict asserts on the timeline, not on the
file count.

### `acceleratedStages: 0` from `QuantonAccelerationTracker`

This counter covers accelerated Spark query stages only. It does not cover clustering or
compaction procedures, so a zero does not mean the native path was off for those.

## Install and access

### Helm output contains `401`, `unauthorized`, or `invalid` during the Quanton Operator install

The credentials in `onehouse-values.yaml` are wrong or expired. Download a fresh copy from the
Onehouse console. Never print the file's contents. Category: user configuration.

### `permission denied` or `forbidden` when deleting the CRD

Deleting `quantonsparkapplications.quantonsparkoperator.onehouse.ai` needs cluster-admin
rights. Category: access.

### The CRD stays in `Terminating` after `kubectl delete crd`

Run:

```bash
kubectl get crd quantonsparkapplications.quantonsparkoperator.onehouse.ai -o jsonpath='{.metadata.deletionTimestamp}{"\n"}'
kubectl get quantonsparkapplications -A
```

A timestamp plus remaining objects means a `QuantonSparkApplication` still carries a finalizer
that the removed operator can no longer clear. Show the objects. Do not patch a finalizer
without the user's explicit yes, because that abandons the resources the operator would have
cleaned up.
