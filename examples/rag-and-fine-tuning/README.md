# RAG and fine-tuning on the lakehouse

Runnable companions to two blog posts. Both run on Kubernetes through
`QuantonSparkApplication`, on the **Spark 4** image.

| Blog | Manifest | Script |
|------|----------|--------|
| [RAG on Your Lakehouse](https://quanton.dev/blog/rag-on-documents/) | [`quanton-rag-demo.yaml`](quanton-rag-demo.yaml) | [`rag_demo.py`](rag_demo.py) |
| [From Lakehouse Tables to a Fine-Tuning Dataset](https://quanton.dev/blog/fine-tuning-from-lakehouse-tables/) | [`quanton-finetune-demo.yaml`](quanton-finetune-demo.yaml) | [`finetune_demo.py`](finetune_demo.py) |

The corpus is [CUAD](https://huggingface.co/datasets/theatticusproject/cuad) (CC-BY-4.0):
510 real commercial contracts as PDFs, plus a CSV of expert-written clause labels. About
100 MB, fetched on first run. The PDFs are the unstructured side and the labels are the
structured side. One engine reads both, and they are joinable in one SQL statement.

Run the RAG demo first. The fine-tuning demo reads the tables it produces.

## Prerequisites

- minikube running with at least 4 CPUs / 8 GB RAM
- Spark Operator and Quanton Operator installed and `Running`
  (see [`../../README.md`](../../README.md))
- Chart 2.0.6 or newer, with `onehouseConfig.quantonSpark4Image` set in your operator
  values. These jobs declare `sparkVersion: "4.1.0"`, which selects that image.
- Outbound network access, for the corpus and the embedding model

## Run

### 1. RAG

```bash
kubectl apply -f quanton-rag-demo.yaml
kubectl logs -f quanton-rag-demo-driver -n default
```

Step 1 reports what the reader found:

```
+-----------------+---------------+-----------+-----+
|content_type     |parse_status   |parser_used|files|
+-----------------+---------------+-----------+-----+
|application/pdf  |SUCCESS        |pypdfium2  |510  |
|text/html        |SUCCESS        |selectolax |2    |
|application/x-csv|STRUCTURED_DATA|NULL       |1    |
+-----------------+---------------+-----------+-----+
```

The CSV is flagged `STRUCTURED_DATA` and left to the native readers. The demo then reads
it as a table.

Two Hudi tables land, `contract_documents` on **LANCE** base files and
`contract_annotations` on **PARQUET**. Chunks are embedded, and step 4 runs three queries:
a cosine top-5, the same ranking joined against the relational table to check whether the
retrieved contracts carry the expert `Non-Compete` label, and a `GROUP BY` that needs no
vector search. It ends with:

```
[rag-demo] PASS — documents(lance)=512 chunks(lance)=400 annotations(parquet)=...
```

### 2. Fine-tuning

```bash
kubectl apply -f quanton-finetune-demo.yaml
kubectl logs -f quanton-finetune-demo-driver -n default
```

It locates each expert-annotated clause span inside the chunk that contains it, builds
SFT rows from those, and exports twice: validated Together JSONL, and tokenized parquet
with a token manifest. Both `_manifest.json` files are printed. Nothing is uploaded; the
manifest's `load` section names the upload command.

## Scale

`rag_demo.py` takes a chunk cap as its second argument, defaulting to **400** so a
minikube run finishes in minutes:

```yaml
arguments:
  - "/data/rag-demo"
  - "400"          # 0 embeds every chunk (~17.8k on the full corpus)
```

All 510 contracts are always parsed and written. The cap applies only to embedding. On a
real cluster, raise it or pass `0`.

## Notes

**`sparkVersion` selects the image.** `sparkVersion: "4.1.0"` with the default accelerator
(`native`) resolves to `onehouseConfig.quantonSpark4Image`; the `image:` field is a
placeholder. Quote the version so YAML reads it as a string.

**Hudi and Lance settings live in `sparkConf`.** The operator builds `spark.properties`
from the CRD, so `extraClassPath`, `spark.sql.extensions`, the Kryo serializer and the
Hoodie registrator are set there.

**`sentence-transformers` is installed by the job.** The image ships
`quanton_unstructured`, `quanton_llm_training`, Hudi and Lance; embedding libraries are
job code. `rag_demo.py` installs `sentence-transformers` from the CPU wheel index, which
skips the CUDA packages. The fine-tuning demo installs nothing. If you use
`sentence-transformers` under `mapInPandas` in your own job, `ensure_sentence_transformers()`
in [`rag_demo.py`](rag_demo.py) shows how to install once per executor across reused
Python workers.

## Storage

Both jobs share one `ReadWriteOnce` PVC mounted at `/data/rag-demo` on the driver and the
executor, so the executor count is 1. On a real cluster, point the arguments at object
storage and drop the PVC. Use `s3://` rather than `s3a://`, since Lance and pyarrow use
their own object-store layers, and set `fs.s3.impl=org.apache.hadoop.fs.s3a.S3AFileSystem`
so Hadoop resolves the same paths.
