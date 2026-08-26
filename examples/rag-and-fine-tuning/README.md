# RAG and fine-tuning on the lakehouse

Runnable companions to two blog posts. Both run on Kubernetes through
`QuantonSparkApplication`, on the **Spark 4** image.

| Blog | Manifest | Script |
|------|----------|--------|
| [RAG on Your Lakehouse](https://quanton.dev/blog/rag-on-documents/) | [`quanton-rag-demo.yaml`](quanton-rag-demo.yaml) | [`rag_demo.py`](rag_demo.py) |
| [From Lakehouse Tables to a Fine-Tuning Dataset](https://quanton.dev/blog/fine-tuning-from-lakehouse-tables/) | [`quanton-finetune-demo.yaml`](quanton-finetune-demo.yaml) | [`finetune_demo.py`](finetune_demo.py) |

The corpus is [CUAD](https://huggingface.co/datasets/theatticusproject/cuad) (CC-BY-4.0):
510 real commercial contracts as PDFs, plus a CSV of clause labels that lawyers wrote.
About 100 MB, fetched on first run. The PDFs are the unstructured side and the labels are
the structured side — the whole point is that one engine reads both and they end up
joinable in one SQL statement.

Run the RAG demo first. The fine-tuning demo reads the tables it produces.

## Prerequisites

- minikube running with at least 4 CPUs / 8 GB RAM
- Spark Operator and Quanton Operator installed and `Running`
  (see [`../../README.md`](../../README.md))
- `onehouseConfig.quantonSpark4Image` set in your operator values — these jobs declare
  `sparkVersion: "4.1.0"`, which is what selects that image
- Outbound network access, for the corpus and the embedding model

## Run

### 1. RAG

```bash
kubectl apply -f quanton-rag-demo.yaml
kubectl logs -f quanton-rag-demo-driver -n default
```

Step 1 reports what the reader found, and this part is deterministic:

```
+-----------------+---------------+-----------+-----+
|content_type     |parse_status   |parser_used|files|
+-----------------+---------------+-----------+-----+
|application/pdf  |SUCCESS        |pypdfium2  |510  |
|text/html        |SUCCESS        |selectolax |2    |
|application/x-csv|STRUCTURED_DATA|NULL       |1    |
+-----------------+---------------+-----------+-----+
```

The CSV is flagged `STRUCTURED_DATA` rather than dragged through a text extractor — the
reader leaves it to the native readers, and the demo then reads it as a table.

Then two Hudi tables land, `contract_documents` on **LANCE** base files and
`contract_annotations` on **PARQUET**, chunks are embedded, and step 4 runs three
queries: a cosine top-5, the same ranking joined against the relational table to check
whether the retrieved contracts really carry the expert `Non-Compete` label, and a
`GROUP BY` that needs no vector search at all. It ends with:

```
[rag-demo] PASS — documents(lance)=512 chunks(lance)=400 annotations(parquet)=...
```

### 2. Fine-tuning

```bash
kubectl apply -f quanton-finetune-demo.yaml
kubectl logs -f quanton-finetune-demo-driver -n default
```

It locates each expert-annotated clause span inside the chunk that contains it, builds
SFT rows from those, and exports twice — validated Together JSONL, and tokenized parquet
with a token manifest — printing both `_manifest.json` files. Nothing is uploaded
anywhere; the manifest's `load` section names the command you still owe.

## Scale

`rag_demo.py` takes a chunk cap as its second argument, defaulting to **400** so a
minikube run finishes in minutes:

```yaml
arguments:
  - "/data/rag-demo"
  - "400"          # 0 embeds every chunk (~17.8k on the full corpus)
```

All 510 contracts are always parsed and written — the cap applies only to embedding,
which is the expensive half. On a real cluster, raise it or pass `0`.

## Notes

Three things in these manifests are not obvious, and all three are load-bearing.

**The image is chosen by `sparkVersion`, not by `image:`.** The operator overrides any
image in the job spec. `sparkVersion: "4.1.0"` plus the default accelerator (`native`)
resolves to `onehouseConfig.quantonSpark4Image`. Quote the version — an unquoted
`4.1` is a YAML number and the job is rejected.

**The Hudi and Lance config is repeated in `sparkConf`.** The Spark 4 image bakes
`extraClassPath`, `spark.sql.extensions`, the Kryo serializer and the Hoodie registrator
into its `spark-defaults.conf`, but the operator regenerates `spark.properties` from the
CRD and never reads that file. Drop those lines and neither format is available at
runtime.

**Embeddings are installed by the job, on purpose.** The image ships
`quanton_unstructured`, `quanton_llm_training`, Hudi and Lance, but deliberately not
`sentence-transformers` — torch would roughly double the image, and embedding is job
code. `rag_demo.py` installs it against the CPU wheel index; the default index pulls
`nvidia/` and `triton/`, about 3.5 GB of CUDA that never runs on a CPU node. The
fine-tuning demo installs nothing at all.

If you use `sentence-transformers` under `mapInPandas` in your own job, read
`ensure_sentence_transformers()` in [`rag_demo.py`](rag_demo.py) before writing your own.
Spark reuses Python workers, and the image already ships a torch-less `transformers`, so
a worker that imports while the install is still in flight caches that copy in
`sys.modules` permanently and fails with `NameError: name 'torch' is not defined` even
after the install completes. The fix is a marker file checked under an `flock`, plus
purging `transformers` from `sys.modules`.

## Storage

Both jobs share one `ReadWriteOnce` PVC mounted at `/data/rag-demo` on the driver and the
executor, which is why the executor count is 1 — the corpus and the Hudi tables have to
be visible to both pods. On a real cluster, point the arguments at object storage
instead and drop the PVC. Use `s3://` rather than `s3a://`: Lance's base-file writer is
native Rust with its own object-store layer and has no `s3a` provider, and pyarrow does
not recognise `s3a` either. Register `fs.s3.impl` so Hadoop resolves the same paths.
