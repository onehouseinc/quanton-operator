#!/usr/bin/env python3
"""RAG on your lakehouse — the four steps from the blog, on Kubernetes.

  https://quanton.dev/blog/rag-on-documents/

  step 1  spark.read.format("quanton_unstructured")  -> one row per file
  step 2  -> contract_documents   Hudi COW, LANCE base files
          -> contract_annotations Hudi COW, PARQUET base files (the clause labels)
  step 3  explode chunks -> BGE embeddings via mapInPandas -> contract_chunks (LANCE)
  step 4  retrieval as SQL: cosine top-k, the same statement joined against the
          relational table, and a GROUP BY that needs no vector search at all

Corpus is CUAD (real commercial contracts, CC-BY-4.0), ~100 MB, fetched on first run.

Usage: rag_demo.py <data_dir> [max_chunks]
"""

import os
import subprocess
import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

DATA = sys.argv[1] if len(sys.argv) > 1 else "/data/rag-demo"
# Parsing all 510 contracts is cheap; embedding every chunk is not. 400 chunks keeps
# a minikube run to a few minutes. Raise it (or pass 0 for all ~17.8k) on a real cluster.
MAX_CHUNKS = int(sys.argv[2]) if len(sys.argv) > 2 else 400

DUMP, TABLES = f"{DATA}/dump", f"{DATA}/tables"
MODEL = "BAAI/bge-base-en-v1.5"   # 768-dim, Apache-2.0, 512-token window
EMBED_SCHEMA = ("uri string, file_name string, pos int, chunk string, "
                "chunk_id string, embedding array<float>")

PYDEPS = "/tmp/pydeps"
os.environ.setdefault("HF_HOME", f"{DATA}/hf")


def ensure_sentence_transformers():
    """Install sentence-transformers into PYDEPS once per pod, then import it.

    The image ships quanton_unstructured, Hudi and Lance, but deliberately not
    sentence-transformers: torch would roughly double the image, and embedding is job
    code rather than connector code. So the job installs it. Three things this has to
    get right, all of them learned by getting them wrong first:

    1. --index-url .../whl/cpu. The default wheel drags in nvidia/ and triton/ -- about
       3.5 GB of CUDA runtime that never executes on a CPU node. Pinning the CPU build
       takes the install from ~5.0 GB to ~1.4 GB.

    2. transformers is named explicitly, with --upgrade. The image already ships it (via
       quanton-llm-training[tokenized]) resolved against NO torch, so a bare
       `--target PYDEPS sentence-transformers` finds it already satisfied and skips it.
       That leaves sentence_transformers loading from PYDEPS while transformers loads
       from the image's site-packages -- a torch-less transformers under a torch that
       now exists. It then detects torch as available and dies in a class body where
       torch was never bound: "NameError: name 'torch' is not defined".

    3. A marker file, checked under the lock, plus a sys.modules purge. Every Python
       worker on an executor calls this and they share one /tmp, so the install must
       happen once. Testing importability instead of a marker is a time-of-check race:
       pip writes --target incrementally, so a worker can import a half-written tree.
       And because Spark reuses Python workers, a worker that imported during that
       window keeps the image's transformers in sys.modules forever -- when a submodule
       raises, Python evicts only that submodule and leaves the parent package cached.
       Purging it is what makes the retry actually recover.
    """
    import fcntl

    if PYDEPS not in sys.path:
        sys.path.insert(0, PYDEPS)

    marker = os.path.join(PYDEPS, ".install-complete")
    os.makedirs(PYDEPS, exist_ok=True)

    with open("/tmp/pydeps.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not os.path.exists(marker):
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", "--no-cache-dir", "--quiet",
                "--target", PYDEPS, "--upgrade",
                "--index-url", "https://download.pytorch.org/whl/cpu",
                "--extra-index-url", "https://pypi.org/simple",
                "sentence-transformers", "transformers",
            ])
            with open(marker, "w") as m:
                m.write("ok")
        import importlib
        importlib.invalidate_caches()

    # Drop any transformers this interpreter cached from the image before PYDEPS existed.
    for name in [m for m in list(sys.modules)
                 if m == "transformers" or m.startswith("transformers.")]:
        del sys.modules[name]

    import sentence_transformers  # noqa: F401


def ensure_corpus():
    """Fetch CUAD and stage a NAS-style mixed dump: 510 PDFs + the annotations CSV."""
    if os.path.isdir(DUMP) and len(os.listdir(DUMP)) > 100:
        print(f"[rag-demo] corpus already staged at {DUMP}", flush=True)
        return
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "--quiet", "--target", PYDEPS, "huggingface_hub"])
    if PYDEPS not in sys.path:
        sys.path.insert(0, PYDEPS)
    import shutil
    from pathlib import Path

    from huggingface_hub import snapshot_download

    raw = Path(DATA) / "cuad"
    snapshot_download(
        repo_id="theatticusproject/cuad", repo_type="dataset", local_dir=raw,
        allow_patterns=["CUAD_v1/full_contract_pdf/**", "CUAD_v1/master_clauses.csv"],
    )
    dump = Path(DUMP)
    dump.mkdir(parents=True, exist_ok=True)
    pdfs = [p for p in (raw / "CUAD_v1" / "full_contract_pdf").rglob("*")
            if p.suffix.lower() == ".pdf"]
    for p in pdfs:
        shutil.copy2(p, dump / p.name)
    # The CSV is a structured straggler on purpose: the reader flags it rather than
    # dragging it through a text extractor.
    shutil.copy2(raw / "CUAD_v1" / "master_clauses.csv", dump / "master_clauses.csv")
    print(f"[rag-demo] staged {len(pdfs)} PDFs + annotations CSV -> {dump}", flush=True)


def write_hudi(df, name, record_key, precombine, base_format):
    """COW Hudi write. LANCE base files need Hudi's Spark-native record type."""
    path = f"{TABLES}/{name}"
    w = (df.write.format("hudi")
         .option("hoodie.table.name", name)
         .option("hoodie.table.base.file.format", base_format)
         .option("hoodie.datasource.write.table.type", "COPY_ON_WRITE")
         .option("hoodie.datasource.write.recordkey.field", record_key)
         .option("hoodie.datasource.write.precombine.field", precombine))
    if base_format == "LANCE":
        # The Lance writer only exists in HoodieSparkFileWriterFactory; without the SPARK
        # record type the write routes through the Avro factory and throws
        # "Lance base file format is currently only supported with the Spark engine".
        w = (w.option("hoodie.datasource.write.record.merger.impls",
                      "org.apache.hudi.DefaultSparkRecordMerger")
             .option("hoodie.write.record.merge.mode", "COMMIT_TIME_ORDERING"))
    w.mode("overwrite").save(path)
    return path


ensure_corpus()
# Before the session, and before anything can import the image's torch-less transformers.
ensure_sentence_transformers()

spark = SparkSession.builder.appName("rag_demo").getOrCreate()
spark.sparkContext.setLogLevel("WARN")


def read_hudi(name):
    return spark.read.format("hudi").load(f"{TABLES}/{name}")


# --- Step 1 -- read the documents ------------------------------------------
import quanton_unstructured

quanton_unstructured.register(spark)

docs = spark.read.format("quanton_unstructured").load(DUMP)
docs.cache()

print("\n[rag-demo] === step 1: what was lying in the dump ===", flush=True)
(docs.groupBy("content_type", "parse_status", "parser_used")
     .agg(F.count("uri").alias("files"))
     .orderBy(F.desc("files")).show(20, truncate=False))

# A file the reader cannot parse does not stop the job -- it lands as a row carrying the
# status and the reason, so failures stay queryable.
failures = docs.where(F.col("parse_status") == "FAILED")
if failures.select("uri").head(1):
    failures.select("uri", "parse_error").show(5, truncate=80)

# --- Step 2 -- land it in Hudi on Lance base files -------------------------
parsed = (docs.where(F.col("parse_status") == "SUCCESS")
          .withColumn("file_name", F.element_at(F.split(F.col("uri"), "/"), -1))
          .select("uri", "file_name", "size", "content_type",
                  "text", "chunks", "parser_used"))
print(f"[rag-demo] contract_documents (LANCE) -> "
      f"{write_hudi(parsed, 'contract_documents', 'uri', 'size', 'LANCE')}", flush=True)

raw = (spark.read.option("header", True).option("multiLine", True).option("escape", '"')
       .csv(f"{DUMP}/master_clauses.csv"))


@F.udf("array<string>")
def parse_spans(cell):
    """Clause columns hold a python-repr list of annotated spans; '[]' when absent."""
    import ast
    if not cell or cell.strip() in ("", "[]"):
        return []
    try:
        v = ast.literal_eval(cell)
        return [str(s) for s in v] if isinstance(v, list) else [str(v)]
    except (ValueError, SyntaxError):
        return [cell]


categories = [c for c in raw.columns
              if c != "Filename" and not c.endswith("-Answer")
              and f"{c}-Answer" in raw.columns]
long_rows = F.explode(F.array(*[
    F.struct(F.lit(c).alias("clause_type"),
             F.col(f"`{c}`").alias("clause_text"),
             F.col(f"`{c}-Answer`").alias("answer"))
    for c in categories])).alias("kv")

annotations = (raw.select(F.col("Filename").alias("file_name"), long_rows)
               .select("file_name",
                       F.col("kv.clause_type").alias("clause_type"),
                       parse_spans(F.col("kv.clause_text")).alias("clause_spans"),
                       F.col("kv.answer").alias("answer"))
               .where(F.size("clause_spans") > 0)
               .withColumn("annotation_id", F.concat_ws("::", "file_name", "clause_type")))
print(f"[rag-demo] contract_annotations (PARQUET) -> "
      f"{write_hudi(annotations, 'contract_annotations', 'annotation_id', 'file_name', 'PARQUET')}",
      flush=True)

# --- Step 3 -- embed the chunks --------------------------------------------
chunks = (read_hudi("contract_documents")
          .select("uri", "file_name", F.posexplode("chunks").alias("pos", "chunk"))
          .withColumn("chunk_id", F.concat_ws("#", "uri", "pos")))
if MAX_CHUNKS:
    chunks = chunks.limit(MAX_CHUNKS)

# Hudi 1.2.0 bug: an empty-projection scan returns 0 rows on a lance table, so a bare
# count() lies. Always aggregate over a column.
n_chunks = chunks.agg(F.count("chunk_id").alias("c")).first()["c"]
print(f"\n[rag-demo] === step 3: embedding {n_chunks} chunks with {MODEL} ===", flush=True)


def embed_partition(iterator):
    """mapInPandas: the model loads once per worker, not once per row."""
    ensure_sentence_transformers()
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL, device="cpu")
    for pdf in iterator:
        vecs = model.encode(pdf["chunk"].tolist(), batch_size=32,
                            normalize_embeddings=True, show_progress_bar=False)
        pdf["embedding"] = [v.tolist() for v in vecs]
        yield pdf


embedded = chunks.repartition(4).mapInPandas(embed_partition, schema=EMBED_SCHEMA)
print(f"[rag-demo] contract_chunks (LANCE) -> "
      f"{write_hudi(embedded, 'contract_chunks', 'chunk_id', 'pos', 'LANCE')}", flush=True)

# --- Step 4 -- retrieve with SQL -------------------------------------------
read_hudi("contract_chunks").createOrReplaceTempView("chunks")
read_hudi("contract_annotations").createOrReplaceTempView("annotations")
read_hudi("contract_documents").createOrReplaceTempView("documents")

question = "Which contracts prevent a party from competing or working with competitors?"
from sentence_transformers import SentenceTransformer

q_vec = (SentenceTransformer(MODEL, device="cpu")
         .encode([question], normalize_embeddings=True)[0].tolist())
spark.sql(f"SELECT array({','.join(f'{x}F' for x in q_vec)}) v").createOrReplaceTempView("q")

# Both vectors have unit length, so the dot product IS the cosine.
print(f"\n[rag-demo] === step 4: retrieval is a query ===\n{question!r}", flush=True)
spark.sql("""
    WITH scored AS (
        SELECT c.file_name, c.pos, c.chunk,
               aggregate(zip_with(c.embedding, q.v, (a, b) -> a * b),
                         0.0F, (acc, x) -> acc + x) AS cosine
        FROM chunks c CROSS JOIN q
    )
    SELECT file_name, pos, round(cosine, 4) AS cosine,
           substring(chunk, 1, 100) AS excerpt
    FROM scored ORDER BY cosine DESC LIMIT 5
""").createOrReplaceTempView("top_hits")
spark.table("top_hits").show(truncate=False)

# Context needs more than similarity. The embedding does not hold "expert-labeled
# Non-Compete"; a column in the relational table does, and it sits next to the chunks.
print("[rag-demo] === the same statement joins the relational table ===", flush=True)
spark.sql("""
    SELECT t.file_name, t.cosine,
           CASE WHEN a.annotation_id IS NOT NULL
                THEN 'YES (expert-labeled)' ELSE 'no' END AS has_non_compete
    FROM top_hits t
    LEFT JOIN annotations a
      ON a.file_name = t.file_name AND a.clause_type = 'Non-Compete'
    ORDER BY t.cosine DESC
""").show(truncate=False)

# Some questions are not retrieval at all. A CSV supplied the labels, PDFs supplied the
# chunks, and one GROUP BY reads both -- across PARQUET and LANCE base files.
print("[rag-demo] === coverage: one GROUP BY over parquet labels x lance chunks ===", flush=True)
spark.sql("""
    SELECT a.clause_type,
           COUNT(a.annotation_id)  AS annotated_contracts,
           SUM(size(d.chunks))     AS retrievable_chunks
    FROM annotations a
    JOIN documents d ON a.file_name = d.file_name
    WHERE a.clause_type IN ('Anti-Assignment', 'Termination For Convenience',
                            'Exclusivity', 'Non-Compete', 'Ip Ownership Assignment')
    GROUP BY a.clause_type ORDER BY annotated_contracts DESC
""").show(truncate=False)

n_docs = spark.sql("SELECT COUNT(uri) c FROM documents").first()["c"]
n_vec = spark.sql("SELECT COUNT(chunk_id) c FROM chunks").first()["c"]
n_ann = spark.sql("SELECT COUNT(annotation_id) c FROM annotations").first()["c"]
print(f"\n[rag-demo] PASS — documents(lance)={n_docs} "
      f"chunks(lance)={n_vec} annotations(parquet)={n_ann}", flush=True)
spark.stop()
