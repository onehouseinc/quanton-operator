---
name: run-rag-demo
description: Run the RAG-on-the-lakehouse and fine-tuning demos on minikube — parses 510 real contract PDFs into Hudi tables on LANCE and PARQUET base files, embeds chunks, answers a question with cosine similarity joined to expert labels in one SQL statement, then exports a validated fine-tuning dataset
allowed-tools: Bash, Read, AskUserQuestion
---

# Run the RAG and fine-tuning demos

You are a guided demo agent for the two AI demos in `examples/rag-and-fine-tuning/`. Run
the chosen demo, give live progress, and report whether it passed.

| Blog | Manifest | Script |
|---|---|---|
| [RAG on Your Lakehouse](https://quanton.dev/blog/rag-on-documents/) | `examples/rag-and-fine-tuning/quanton-rag-demo.yaml` | `rag_demo.py` |
| [From Lakehouse Tables to a Fine-Tuning Dataset](https://quanton.dev/blog/fine-tuning-from-lakehouse-tables/) | `examples/rag-and-fine-tuning/quanton-finetune-demo.yaml` | `finetune_demo.py` |

Each manifest is self-contained: a ConfigMap with the script inline, plus a
QuantonSparkApplication. The RAG manifest also carries the shared PVC.

**The fine-tuning demo reads the tables the RAG demo writes.** Never run it on its own
against an empty PVC — check for `contract_chunks` first, and run the RAG demo if it is
missing.

## Phase 0: Interactive configuration

Use AskUserQuestion. Keep it short.

### Q1: Which demo?

> "Which demo should I run?"

Options:
- **RAG only** — parse 510 contracts, embed chunks, retrieve with SQL.
- **Fine-tuning only** — export an SFT dataset from tables a previous run produced.
  Requires `contract_chunks` to exist already.
- **Both** — RAG then fine-tuning, sequentially. This is the intended path.

### Q2: How many chunks to embed?

> "Embedding is the slow half. How many chunks?"

Options:
- **400 (default)** — a few minutes on minikube.
- **2000** — a richer retrieval result, roughly 5x longer.
- **All (~17.8k)** — pass `0`. Use a real cluster, not minikube; budget about an hour.

Patch the second argument of `spec.sparkApplicationSpec.arguments` in the RAG manifest to
match the answer. Do not edit anything else.

### Q3: Cluster check (no question — just verify)

```bash
kubectl config current-context
```

Confirm it is `minikube`. If it is any other context, **stop** and tell the user to run
`kubectl config use-context minikube` first — these manifests write to a PVC in
`default` and are not meant for a shared cluster.

Then confirm the operator can actually serve a Spark 4 image:

```bash
kubectl get configmap quanton-operator-config -n quanton-operator \
  -o jsonpath='{.data.config\.json}' | python3 -m json.tool | grep -i spark4
```

If `quantonSpark4Image` is missing or empty, stop. The jobs declare
`sparkVersion: "4.1.0"`, which requires that image. Tell the user to set
`onehouseConfig.quantonSpark4Image` in their operator values (chart 2.0.6 or newer).

## Phase 1: Run

```bash
kubectl apply -f examples/rag-and-fine-tuning/quanton-rag-demo.yaml
kubectl logs -f quanton-rag-demo-driver -n default
```

Report progress as the phases land. The first run downloads the corpus (~100 MB) and
installs `sentence-transformers`, so expect several quiet minutes before step 1.

Step 1 output is deterministic — check it exactly:

```
|application/pdf  |SUCCESS        |pypdfium2  |510  |
|text/html        |SUCCESS        |selectolax |2    |
|application/x-csv|STRUCTURED_DATA|NULL       |1    |
```

If the PDF count is not 510, the corpus download was truncated. Delete the PVC and rerun
rather than reporting a partial result.

Then for fine-tuning:

```bash
kubectl apply -f examples/rag-and-fine-tuning/quanton-finetune-demo.yaml
kubectl logs -f quanton-finetune-demo-driver -n default
```

## Phase 2: Report

For the RAG demo, show the user three things and say what each one demonstrates:

1. **The routing table** — one engine picked a parser per file, and left the CSV to the
   native readers rather than a text extractor.
2. **The cosine top-5 joined to the expert labels** — how many of the retrieved contracts
   carry the expert `Non-Compete` label. This is the accuracy signal. Do not present a
   high cosine as correctness on its own.
3. **The coverage `GROUP BY`** — a CSV supplied the labels, PDFs supplied the chunks, and
   one query read both across PARQUET and LANCE base files. It needs no vector search,
   which is the argument for keeping vectors next to the relational data.

For fine-tuning, show the two `_manifest.json` blocks and point out that `upload` is
absent — the writer produces the dataset and never talks to a provider.

Both end with a `PASS —` line. Quote it.

## Troubleshooting

**`NameError: name 'torch' is not defined`** in a `mapInPandas` task. A Python worker
imported `transformers` before the install finished. Spark retries the task and the retry
succeeds. If every attempt fails, check that the script in the ConfigMap matches
`rag_demo.py` on disk.

**Driver evicted, `Evicted pod: Underutilized`.** Only on clusters running Karpenter, not
minikube. Add `spark.kubernetes.driver.annotation.karpenter.sh/do-not-disrupt: "true"`.

**`No object store provider found for scheme: 's3a'`.** You pointed the demo at object
storage using `s3a://`. Lance uses its own object-store layer, which does not recognise
`s3a`. Use `s3://` and register `fs.s3.impl=org.apache.hadoop.fs.s3a.S3AFileSystem`.

## Cleanup

```bash
kubectl delete -f examples/rag-and-fine-tuning/quanton-finetune-demo.yaml
kubectl delete -f examples/rag-and-fine-tuning/quanton-rag-demo.yaml
```

Deleting the RAG manifest removes the PVC and therefore the corpus, so a rerun downloads
it again. Ask before deleting if the user may want to rerun.
