---
name: run-rag-demo
description: Run the RAG-on-the-lakehouse demo and the fine-tuning dataset demo on minikube. Parses 510 contract PDFs into Hudi tables on LANCE and PARQUET base files, embeds chunks, answers a question with cosine similarity joined to expert labels in one SQL statement, then exports a validated fine-tuning dataset. Use whenever the user wants to run, demo, or verify RAG, embeddings, vector search in SQL, document parsing, Lance tables, or fine-tuning data export with Quanton.
compatibility: Requires kubectl, a minikube cluster with the Spark Operator and the Quanton Operator installed, and an operator configured with onehouseConfig.quantonSpark4Image (chart 2.0.6 or newer). Network access from the agent's shell to the cluster, and from the cluster to download the corpus and Python packages on first run.
allowed-tools: Bash, Read, Write, AskUserQuestion
metadata:
  version: "3"
---

# Run the RAG and fine-tuning demos

Two demos, two manifests, one shared PVC. The RAG demo parses PDFs, embeds chunks, and answers
a question in SQL. The fine-tuning demo reads the tables the RAG demo wrote and exports a
dataset. **The fine-tuning demo depends on the RAG demo's output.** Never run it against an
empty PVC.

Follow the working rules in `AGENTS.md`. Helper scripts live in `scripts/agent/`. Shared
failure cases live in `docs/troubleshooting.md`.

## Facts this skill relies on

| Item | RAG | Fine-tuning |
|---|---|---|
| Manifest | `examples/rag-and-fine-tuning/quanton-rag-demo.yaml` | `examples/rag-and-fine-tuning/quanton-finetune-demo.yaml` |
| Script (for reading only) | `examples/rag-and-fine-tuning/rag_demo.py` | `examples/rag-and-fine-tuning/finetune_demo.py` |
| App name | `quanton-rag-demo` | `quanton-finetune-demo` |
| Expected driver pod | `quanton-rag-demo-driver` | `quanton-finetune-demo-driver` |
| Arguments | `["/data/rag-demo", "400"]`; the second is the chunk cap, `0` means all | `["/data/rag-demo", ...]` |
| Log prefix | `rag-demo` | `finetune-demo` |
| Verdict line starts with | `[rag-demo] PASS —` | `[finetune-demo] PASS —` |
| Blog it reproduces | https://quanton.dev/blog/rag-on-documents/ | https://quanton.dev/blog/fine-tuning-from-lakehouse-tables/ |

Shared: PVC `rag-and-fine-tuning-demo-pvc`, declared in the RAG manifest. Namespace `default`.
Both jobs declare `sparkVersion: "4.1.0"`, so the operator must know a Spark 4 image.
`examples/rag-and-fine-tuning/README.md` is the source of truth if this table and the
repository disagree.

## Procedure

### Step 1: Confirm the cluster and the Spark 4 image

Run:

```bash
scripts/agent/check-cluster.sh --require-context minikube --require-charts spark-operator,quanton-operator
kubectl get configmap quanton-operator-config -n quanton-operator -o jsonpath='{.data.config\.json}' | python3 -c 'import json,sys; c=json.load(sys.stdin); print("quantonSpark4Image =", repr(c.get("quantonSpark4Image","")))'
```

Continue only if the first command ends with `result: OK`. On `context check: FAIL`, tell the
user which context is active and stop; these manifests write to a PVC in `default` and are not
meant for a shared cluster. On `chart <name>: MISSING`, point the user to the
`setup-and-run-example` skill and stop.

If `quantonSpark4Image` prints an empty string, stop. Tell the user to set
`onehouseConfig.quantonSpark4Image` in their operator values (chart 2.0.6 or newer) and
upgrade the release.

### Step 2: Ask which demo and how many chunks

Ask: "Which demo should I run?" with options **RAG**, **Fine-tuning** (needs a prior RAG run),
**Both** (the intended path).

If RAG is included, Ask: "Embedding is the slow half. How many chunks?" with options
**400** (default, a few minutes on minikube), **2000** (about 5x longer), **All** (pass `0`;
about an hour; a real cluster is a better fit than minikube).

### Step 3: If fine-tuning only, prove the RAG output exists

Run:

```bash
scripts/agent/app-verdict.sh --name quanton-rag-demo --prefix rag-demo
kubectl get pvc rag-and-fine-tuning-demo-pvc -n default --no-headers 2>/dev/null || echo "PVC not found"
```

Continue to fine-tuning only if the first command printed `verdict: PASS` and the PVC exists.
Otherwise tell the user the RAG demo has to run first and Ask whether to run it now.

### Step 4: Run the RAG demo

1. If the chunk count is not 400, write a patched copy. Never edit the checked-in manifest.
   Save this as a temporary Python file and run it with the chunk count as its only argument:

```python
import re, sys, pathlib, tempfile
n = sys.argv[1]
src = pathlib.Path("examples/rag-and-fine-tuning/quanton-rag-demo.yaml").read_text()
out, count = re.subn(r'(arguments:\n\s+- "/data/rag-demo"\n\s+- )"400"', rf'\g<1>"{n}"', src)
assert count == 1, f"expected exactly one arguments block, patched {count}"
dst = pathlib.Path(tempfile.mkdtemp(), "quanton-rag-demo.yaml")
dst.write_text(out)
print(dst)
```

The script prints the patched path; use it in the next command. If the assertion fails, the
manifest changed shape. Read it and stop.

2. Apply and Wait. The first run downloads the corpus (about 100 MB) and installs
`sentence-transformers`, so expect several quiet minutes before the script's step 1 prints.

```bash
kubectl apply -f <manifest-or-patched-path>
scripts/agent/wait-for-app.sh --kind quantonsparkapplication --name quanton-rag-demo --log-prefix rag-demo --max-seconds 480 --interval 30
```

The wait script prints one status line per 30 seconds with the last `[rag-demo]` marker and
exits 0 at a terminal phase. If it exits 2 with `deadline ... passed`, tell the user the phase
and the marker you saw in one line, then run it again.

3. Check the deterministic routing table. Run:

```bash
kubectl logs quanton-rag-demo-driver -n default | grep -E "application/pdf|application/x-csv"
```

Expected, character for character:

```
|application/pdf  |SUCCESS        |pypdfium2  |510  |
|application/x-csv|STRUCTURED_DATA|NULL       |1    |
```

If the PDF count is not 510, the corpus download was truncated. Say the count you saw. Do not
present the run as complete. Ask whether to delete the PVC and rerun.

4. Read the verdict:

```bash
scripts/agent/app-verdict.sh --name quanton-rag-demo --prefix rag-demo
```

Quote the marker lines verbatim. The demo passed only if the script printed `verdict: PASS`.

### Step 5: Run the fine-tuning demo

```bash
kubectl apply -f examples/rag-and-fine-tuning/quanton-finetune-demo.yaml
scripts/agent/wait-for-app.sh --kind quantonsparkapplication --name quanton-finetune-demo --log-prefix finetune-demo --max-seconds 480 --interval 30
```

Then:

```bash
scripts/agent/app-verdict.sh --name quanton-finetune-demo --prefix finetune-demo
kubectl logs quanton-finetune-demo-driver -n default | grep -A12 "_manifest.json"
```

### Step 6: Offer cleanup

Deleting the RAG manifest deletes the PVC and the downloaded corpus, so a rerun downloads it
again. Ask: "Clean up? Deleting removes the corpus and both tables." On yes:

```bash
kubectl delete -f examples/rag-and-fine-tuning/quanton-finetune-demo.yaml --ignore-not-found
kubectl delete -f examples/rag-and-fine-tuning/quanton-rag-demo.yaml --ignore-not-found
```

## Report

For the RAG demo, show three things from the driver log and say what each demonstrates. Quote
the log; do not paraphrase numbers.

1. **The routing table.** One engine picked a parser per file type and left the CSV to the
   native readers.
2. **The cosine top-5 joined to expert labels.** How many retrieved contracts carry the expert
   `Non-Compete` label. That is the accuracy signal. A high cosine score alone is not
   correctness; say so.
3. **The coverage `GROUP BY`.** A CSV supplied labels, PDFs supplied chunks, one query read
   both across PARQUET and LANCE base files with no separate vector store.

For fine-tuning, show the two `_manifest.json` blocks and point out that no `upload` key is
present: the writer produces the dataset and never contacts a provider.

Quote each demo's `PASS —` line verbatim. If a demo has no `PASS` line, the report says so.

## Failure handling

- **`NameError: name 'torch' is not defined` in a `mapInPandas` task.** A Python worker
  imported `transformers` before the install finished. Spark retries the task. If every attempt
  fails, compare the ConfigMap script with `rag_demo.py` on disk.
- **Phase `Failed` with no marker lines, an `Evicted pod: Underutilized` event, or
  `No object store provider found for scheme: 's3a'`.** Read the matching entry in
  `docs/troubleshooting.md`. An image pull error is an environment problem; a Python traceback
  inside the script is a demo problem.
