# Open Terminology Server

> Warning: Open Terminology Server is currently in testing mode. It is not
> intended for production use or clinical decision-making without independent
> validation, operational hardening, and governance review.

Open Terminology Server is a small, read-heavy terminology server backed by
Postgres and pgvector. Each terminology is stored as one denormalized row per
concept in its own table, and each embedding model gets its own physical vector
table.

The API supports:

- imported terminologies: SNOMED CT, LOINC, ICD-10-CM, and ICD-11 MMS
- custom terminologies created through the API
- lexical search over denormalized concept rows
- vector search with model-scoped embeddings
- optional ancestor and semantic-tag filters
- terminology versions, with one default version per terminology

## Third-Party Terminology Notice

SNOMED CT, LOINC, ICD-10-CM, and ICD-11 MMS are third-party terminology
systems. Their names, trademarks, code systems, and release data belong to
their respective owners. Open Terminology Server is independent and is not
endorsed by SNOMED International, Regenstrief Institute, the LOINC Committee,
WHO, CMS, NCHS, or related publishers. See [NOTICE.md](NOTICE.md).

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Third-party notices](NOTICE.md)
- [SNOMED CT](docs/terminologies/SNOMED.md)
- [LOINC](docs/terminologies/LOINC.md)
- [ICD-10-CM](docs/terminologies/ICD10CM.md)
- [ICD-11 MMS](docs/terminologies/ICD11.md)
- [SNOMED RF2 reference](docs/SNOMED_RF2_REFERENCE.md)

## Docker Setup

Build and start Postgres, the Starlette API, and the Celery worker:

```bash
docker compose up --build
```

Run in the background:

```bash
docker compose up -d --build
```

The API is available at:

```text
http://127.0.0.1:8000
```

Swagger UI is available at:

```text
http://127.0.0.1:8000/docs
```

The Compose stack uses:

- `postgres`: `pgvector/pgvector:pg16`
- `api`: `open-terminology-server:local`
- `worker`: the same image running Celery

Runtime data is mounted from `./data` into `/app/data`, so RF2/LOINC/ICD source
files can stay outside the Docker image.
FastEmbed model files are cached under `data/models/fastembed` by default when
running in Docker.

Useful Docker commands:

```bash
docker compose logs -f api
docker compose logs -f worker
docker compose run --rm api python -m ots.cli --help
docker compose run --rm api python -m ots.cli snomed load -- --help
```

For development with source code bind-mounted into the container:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

The dev override mounts the repository at `/app` and runs:

```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

The Makefile wraps the same Docker commands:

```bash
make up
make dev-up
make logs-api
make dev-logs-api
make migrate
make run
make run snomed load-packages
make run ARGS="snomed load -- --help"
```

For local Ollama from Docker Desktop on macOS, Compose defaults
`OTS_OLLAMA_HOST` to `http://host.docker.internal:11434`. Set `OPENAI_API_KEY`
in your shell or `.env` file when using OpenAI embeddings.

## Local Setup

Start Postgres with pgvector:

```bash
docker compose up -d postgres
```

Install Python packages:

```bash
pipenv install
```

Run the API:

```bash
OTS_DATABASE_URL=postgresql://ots:ots@127.0.0.1:5432/ots \
pipenv run python -m uvicorn app:app --reload
```

Local Swagger UI is available at:

```text
http://127.0.0.1:8000/docs
```

## CLI

Operational commands are grouped under a Typer CLI:

```bash
pipenv run python -m ots.cli --help
```

Command groups:

- `snomed`: RF2 imports, grouped extension imports, SNOMED search text rebuilds
- `loinc`: LOINC imports, cleanup tasks, LOINC search text rebuilds
- `icd`: ICD release downloads and ICD-10-CM/ICD-11 imports
- `common`: embeddings, database dump/restore, lexical indexes, edition resync

Terminology-specific code lives under `ots/terminology/<system>/`. Each system
keeps its registry model in `model.py` and import/maintenance commands in
`scripts/`; the central registry in `ots/terminology/registry.py` is the single
place that imports built-in terminology definitions. `ots/terminologies.py`
exists only as a compatibility shim for older imports.

For detailed options on a delegated command, pass help after `--`, for example:

```bash
pipenv run python -m ots.cli snomed load -- --help
```

## Database Migrations

Schema changes are managed with Alembic. The migration environment reads the
database URL from `OTS_DATABASE_URL`; in Docker Compose this is already set to
the `postgres` service.

Check the current migration:

```bash
docker compose run --rm api alembic current
```

Apply all pending migrations:

```bash
docker compose run --rm api alembic upgrade head
```

Rollback the most recent migration:

```bash
docker compose run --rm api alembic downgrade -1
```

Rollback to a specific revision:

```bash
docker compose run --rm api alembic downgrade 20260625_0003
```

Rollback everything:

```bash
docker compose run --rm api alembic downgrade base
```

Inspect migration state:

```bash
docker compose run --rm api alembic heads
docker compose run --rm api alembic history
docker compose run --rm api alembic history --verbose
```

Create a new migration:

```bash
docker compose run --rm api alembic revision -m "describe schema change"
```

For local development without Docker, use the same Alembic commands through
Pipenv:

```bash
OTS_DATABASE_URL=postgresql://ots:ots@127.0.0.1:5432/ots \
pipenv run alembic upgrade head
```

When a migration creates indexes on large terminology tables, prefer doing it
during a maintenance window. For lexical search indexes specifically, the
operational command uses `CREATE INDEX CONCURRENTLY`:

```bash
docker compose run --rm api python -m ots.cli common lexical-indexes
```

## Terminology Editions

The data model is:

```text
Terminology
  Edition/version
    Release package(s)
```

Examples:

```text
snomed
  international-20260601 <- Default
    snomed-international 20260601
  india-20260630
    snomed-international 20260601
    india-core 20260630
    india-drug 20260630
    india-ayush 20260630
```

An edition is the queryable universe. It has one materialized concept table and
one set of version-scoped embedding tables. Release packages describe the raw
inputs included in the edition. Existing unversioned tables are registered as
edition `current` and marked as the default by the migrations.

Import a manually named version and make it the default:

```bash
pipenv run python -m ots.cli snomed load \
  --rf2-dir data/raw/snomed/Snapshot \
  --version 20260601 \
  --package-key snomed-international \
  --package-version 20260601 \
  --default-version \
  --recreate
```

Import a package into a composed edition without changing the default:

```bash
pipenv run python -m ots.cli loinc load \
  --loinc-dir data/raw/loinc/Loinc_2.82 \
  --version 2.82 \
  --package-key loinc \
  --package-version 2.82
```

For SNOMED editions composed from multiple RF2 packages, first preview the
package grouping and then load the zips into a single edition:

```bash
pipenv run python -m ots.cli snomed load-packages \
  --source-dir data/raw \
  --edition-version snomed_india_20260313 \
  --plan-only
```

```bash
pipenv run python -m ots.cli snomed load-packages \
  --source-dir data/raw \
  --edition-version snomed_india_20260313 \
  --base-version 20260601 \
  --default-version
```

All APIs use the default version when `version` is omitted. Pass `version` or
`terminologyVersion` on search, lookup, hierarchy, and status endpoints to query
a specific version:

```bash
curl -H 'x-api-key: open-terminology-server-dev-key' \
  'http://127.0.0.1:8000/search?q=heart&terminology=snomed&version=20260601'
```

Lexical search uses Postgres full-text search plus `pg_trgm` indexes for
substring matches against preferred term, FSN, search text, and code/display
fields. Build or refresh these indexes for existing concept tables with:

```bash
pipenv run python -m ots.cli common lexical-indexes
```

Use `--terminology loinc` or `--version 20260601` to scope the index build.

## Background Jobs

Embedding population can run in a Celery worker with Postgres as both the broker
and result backend. By default the worker uses:

```text
OTS_CELERY_BROKER_URL=sqla+postgresql+psycopg://ots:ots@127.0.0.1:5432/ots
OTS_CELERY_RESULT_BACKEND=db+postgresql+psycopg://ots:ots@127.0.0.1:5432/ots
```

Start the API:

```bash
pipenv run python -m uvicorn app:app --reload
```

Start a worker in another terminal:

```bash
pipenv run celery -A ots.worker.celery_app worker --loglevel=info --pool=threads --concurrency=1
```

Queue an embedding job:

```bash
curl -X POST http://127.0.0.1:8000/embeddings/jobs \
  -H 'x-api-key: open-terminology-server-dev-key' \
  -H 'Content-Type: application/json' \
  -d '{
    "terminology": "snomed",
    "version": "20260601",
    "provider": "openai",
    "model": "text-embedding-3-large",
    "modelKey": "openai:text-embedding-3-large:1536",
    "dimensions": 1536,
    "providerOptions": {
      "timeout": 120,
      "max_retries": 2
    },
    "batchSize": 128,
    "parallelRequests": 4,
    "semanticTags": ["disorder", "finding"],
    "recreateIndex": true
  }'
```

Poll the returned job:

```bash
curl -H 'x-api-key: open-terminology-server-dev-key' \
  http://127.0.0.1:8000/embeddings/jobs/{jobId}
```

In Docker, the worker is already part of Compose:

```bash
docker compose up -d worker
```

Refresh a composed edition from a changed base/core edition:

```bash
pipenv run python -m ots.cli common resync-edition \
  --terminology snomed \
  --source-version international-20260601 \
  --target-version india-20260630
```

Resync copies inherited base rows again and leaves local/extension rows intact.
Inherited rows are tagged in `payload` with `inheritedFromTerminology` and
`inheritedFromEdition`.

## Share A Database

Use a custom-format Postgres dump to share loaded terminologies and embeddings.
The dump includes concept tables, embedding tables, pgvector indexes, and
registry tables.

Recommended Docker Compose dump:

```bash
pipenv run python -m ots.cli common dump-db data/exports/ots.dump
```

Recommended Docker Compose restore:

```bash
docker compose up -d postgres

pipenv run python -m ots.cli common load-db \
  data/exports/ots.dump \
  --clean
```

`--clean` drops existing objects in the target database before restoring them
from the dump. Omit it only when restoring into a fresh empty database.

If local PostgreSQL client tools are installed, the same scripts can use
`OTS_DATABASE_URL` or `--database-url` directly. Local tools must match the
server major version, which is PostgreSQL 16 for the Docker setup:

```bash
pipenv run python -m ots.cli common dump-db data/exports/ots.dump --local

pipenv run python -m ots.cli common load-db \
  data/exports/ots.dump \
  --local \
  --clean
```

## Configuration

Runtime configuration lives in `ots/config.py` as Django-style uppercase
settings read from environment variables at import time.

Common variables:

- `OTS_DATABASE_URL`
- `OTS_SQLALCHEMY_DATABASE_URL`
- `OTS_TERMINOLOGY`
- `OTS_API_KEY`
- `OTS_API_KEY_HEADER`
- `OTS_PUBLIC_PATHS`
- `OTS_EMBEDDING_PROVIDER`
- `OTS_EMBEDDING_MODEL`
- `OTS_EMBEDDING_MODEL_KEY`
- `OTS_EMBEDDING_DIMENSIONS`
- `OTS_EMBEDDING_PARALLEL_REQUESTS`
- `OTS_DISABLE_QUERY_EMBEDDING_CACHE`
- `OTS_QUERY_EMBEDDING_CACHE_SIZE`
- `OTS_OLLAMA_HOST`
- `OTS_FASTEMBED_CACHE_DIR`
- `OTS_FASTEMBED_THREADS`
- `OTS_FASTEMBED_PROVIDERS`
- `OPENAI_API_KEY`
- `OTS_OPENAI_TIMEOUT`
- `OTS_OPENAI_MAX_RETRIES`

Defaults:

```text
OTS_DATABASE_URL=postgresql://ots:ots@127.0.0.1:5432/ots
OTS_API_KEY_HEADER=x-api-key
OTS_API_KEY=open-terminology-server-dev-key
OTS_DISABLE_QUERY_EMBEDDING_CACHE=false
OTS_QUERY_EMBEDDING_CACHE_SIZE=1024
```

Set `OTS_DISABLE_QUERY_EMBEDDING_CACHE=true` to disable the in-process query
embedding cache for speed testing. `OTS_QUERY_EMBEDDING_CACHE_SIZE` controls
the cache capacity when caching is enabled.

## API Basics

Most API routes default to `snomed`. Pass `terminology` or `system` to target a
different loaded terminology.

Health and status:

```bash
curl -H 'x-api-key: open-terminology-server-dev-key' \
  http://127.0.0.1:8000/health

curl -H 'x-api-key: open-terminology-server-dev-key' \
  http://127.0.0.1:8000/terminologies

curl -H 'x-api-key: open-terminology-server-dev-key' \
  http://127.0.0.1:8000/embeddings/status
```

Concept lookup:

```bash
curl -H 'x-api-key: open-terminology-server-dev-key' \
  http://127.0.0.1:8000/concepts/22298006

curl -H 'x-api-key: open-terminology-server-dev-key' \
  http://127.0.0.1:8000/terminologies/loinc/concepts/4548-4
```

Lexical search:

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H 'x-api-key: open-terminology-server-dev-key' \
  -H 'Content-Type: application/json' \
  -d '{"terminology":"snomed", "query":"heart", "mode":"lexical", "limit":10}'
```

Vector search:

```bash
curl -X POST http://127.0.0.1:8000/search/vector \
  -H 'x-api-key: open-terminology-server-dev-key' \
  -H 'Content-Type: application/json' \
  -d '{"terminology":"loinc", "query":"heart", "modelKey":"openai:text-embedding-3-large:1536", "limit":5, "detail":"basic"}'
```

Search responses are basic by default. Basic results include identifiers, terms,
semantic tag, and synonyms. Use `includeDetails: true` or `detail: "full"` to
include the larger denormalized payload.

Add `showQuery: true` or `includeQuery: true` to include the SQL and bound
parameters used for a result. Vector responses redact the embedding parameter.

## Search Filters

Ancestor-scoped search uses precomputed `ancestor_ids` on the concept table:

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H 'x-api-key: open-terminology-server-dev-key' \
  -H 'Content-Type: application/json' \
  -d '{"terminology":"snomed", "query":"heart", "ancestorConceptId":404684003, "includeAncestor":false, "limit":10}'
```

Semantic tags can be passed as `semanticTag` or `semanticTags`:

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H 'x-api-key: open-terminology-server-dev-key' \
  -H 'Content-Type: application/json' \
  -d '{"terminology":"snomed", "query":"heart", "semanticTags":["disorder"], "limit":10}'
```

Children and descendants also read from the denormalized concept table:

```bash
curl -H 'x-api-key: open-terminology-server-dev-key' \
  'http://127.0.0.1:8000/concepts/404684003/children?limit=25'

curl -H 'x-api-key: open-terminology-server-dev-key' \
  'http://127.0.0.1:8000/concepts/404684003/descendants?limit=25'
```

## FHIR Endpoints

The server exposes a small FHIR-compatible surface for consumers such as Care.
Only `ValueSet.compose.include` rules are supported for expansion.

FHIR ValueSet expansion:

```bash
curl -X POST 'http://127.0.0.1:8000/ValueSet/$expand' \
  -H 'x-api-key: open-terminology-server-dev-key' \
  -H 'Content-Type: application/fhir+json' \
  -d '{
    "resourceType": "Parameters",
    "parameter": [
      {"name": "filter", "valueString": "Blood pressure"},
      {"name": "count", "valueInteger": 10},
      {"name": "displayLanguage", "valueString": "en-gb"},
      {
        "name": "valueSet",
        "resource": {
          "resourceType": "ValueSet",
          "compose": {
            "include": [
              {
                "system": "http://snomed.info/sct",
                "filter": [
                  {"property": "concept", "op": "is-a", "value": "404684003"}
                ]
              }
            ]
          }
        }
      },
      {"name": "includeDesignations", "valueBoolean": true}
    ]
  }'
```

Supported include rules:

- `include.concept[].code` for explicit codes
- `include.filter[]` with `property: "concept"` and `op: "is-a"`
- `include.filter[]` with `property: "semanticTag"` and `op: "="` or `op: "in"`

The `is-a` filter uses precomputed hierarchy arrays from the concept table and
returns the parent concept plus descendants. When `filter` text is supplied,
expansion tries vector search first with `OTS_EMBEDDING_MODEL_KEY`; if that
embedding model is unavailable or embedding fails, it falls back to lexical
filtering and reports `searchMode=lexical` in `expansion.parameter`. Default
expansion uses `activeOnly=true`; pass `activeOnly=false` to include inactive
content. Responses include a CARE-friendly top-level `results` array with
`display`, `system`, and `code`, plus the FHIR `expansion.contains` array.
Pass `includeDesignations=true` to include richer result fields such as FSN,
semantic tag, and designations. Lookup responses are detailed by default.

Semantic tag filters can narrow broad SNOMED hierarchy scopes to a specific
abstraction level:

```json
{
  "system": "http://snomed.info/sct",
  "filter": [
    {"property": "concept", "op": "is-a", "value": "763158003"},
    {"property": "semanticTag", "op": "=", "value": "clinical drug"}
  ]
}
```

FHIR lookup:

```bash
curl -H 'x-api-key: open-terminology-server-dev-key' \
  'http://127.0.0.1:8000/CodeSystem/$lookup?system=http://loinc.org&code=2502-3'
```

Terminology-scoped lookup:

```bash
curl -H 'x-api-key: open-terminology-server-dev-key' \
  http://127.0.0.1:8000/terminologies/loinc/lookup/2502-3
```

## Embeddings

Embeddings are populated by `ots.cli common embed`. The command
is resumable by default and skips rows already embedded for the selected
`modelKey`. Use `--refresh` to recompute an existing model.

Embedding providers are registered in `ots/embedding_providers/registry.py`.
The CLI keeps common values as flags (`--provider`, `--model`, `--dimensions`,
`--batch-size`, and so on). Provider-specific values are passed as JSON through
`--provider-options`, or via a JSON file using `--provider-options @file.json`.
Common provider option keys are:

- Ollama: `host`
- OpenAI: `api_key`, `timeout`, `max_retries`
- FastEmbed: `cache_dir`, `threads`, `providers`

Ollama example:

```bash
ollama pull embeddinggemma

pipenv run python -m ots.cli common embed \
  --terminology snomed \
  --version 20260601 \
  --provider ollama \
  --model embeddinggemma \
  --model-key ollama:embeddinggemma \
  --provider-options '{"host":"http://127.0.0.1:11434"}' \
  --batch-size 64
```

Qwen3 Embedding through Ollama can be stored as a full unindexed
`vector(4096)`. Use this when you want to compare model quality without HNSW or
half-vector approximation:

```bash
ollama pull qwen3-embedding

pipenv run python -m ots.cli common embed \
  --terminology loinc \
  --provider ollama \
  --model qwen3-embedding \
  --model-key ollama:qwen3-embedding:4096 \
  --dimensions 4096 \
  --storage-type vector \
  --batch-size 32 \
  --skip-index
```

Use the same command with `--terminology snomed`, `icd10cm`, or `icd11` to
populate Qwen3 embeddings for another terminology. Because this stores a full
4096-dimensional vector without an index, vector search is exact but will scan
the embedding table.

FastEmbed local example:

```bash
pipenv run python -m ots.cli common embed \
  --terminology snomed \
  --version 20260601 \
  --provider fastembed \
  --model BAAI/bge-small-en-v1.5 \
  --model-key fastembed:BAAI/bge-small-en-v1.5:384 \
  --dimensions 384 \
  --provider-options '{"cache_dir":"data/models/fastembed","threads":8}' \
  --batch-size 256 \
  --parallel-requests 1 \
  --semantic-tags disorder,finding \
  --recreate-index
```

Docker:

```bash
make run ARGS="common embed -- --terminology snomed --version 20260601 --provider fastembed --model BAAI/bge-small-en-v1.5 --model-key fastembed:BAAI/bge-small-en-v1.5:384 --dimensions 384 --provider-options '{\"cache_dir\":\"data/models/fastembed\",\"threads\":8}' --batch-size 256 --semantic-tags disorder,finding --recreate-index"
```

FastEmbed uses local ONNX models. The first run downloads the model into the
FastEmbed cache; later runs reuse the cache. Population uses FastEmbed
`passage_embed`, and query-time vector search uses `query_embed`.

OpenAI example:

```bash
export OPENAI_API_KEY=...

pipenv run python -m ots.cli common embed \
  --terminology loinc \
  --provider openai \
  --model text-embedding-3-large \
  --model-key openai:text-embedding-3-large:1536 \
  --dimensions 1536 \
  --provider-options '{"timeout":120,"max_retries":2}' \
  --batch-size 128 \
  --parallel-requests 4 \
  --recreate-index
```

For `text-embedding-3-large` at 3072 dimensions, use `halfvec` storage to keep a
full `vector(3072)` copy for reranking and an indexed `halfvec(3072)` copy for
candidate retrieval:

```bash
pipenv run python -m ots.cli common embed \
  --terminology snomed \
  --provider openai \
  --model text-embedding-3-large \
  --model-key openai:text-embedding-3-large:3072:halfvec \
  --dimensions 3072 \
  --storage-type halfvec \
  --provider-options '{"timeout":120,"max_retries":2}' \
  --batch-size 256 \
  --parallel-requests 4 \
  --recreate-index
```

For `halfvec` models, `vectorSearchStrategy` controls query behavior:

- `halfvec_rerank`: search indexed `embedding_half`, then rerank candidates
  with full `embedding`
- `full_exact`: rank by full `embedding` only, slower but useful for quality
  checks
- `halfvec_only`: rank by indexed `embedding_half` only

Text query embeddings are cached in-process by provider, provider model,
`modelKey`, dimensions, and query text. Vector responses include
`embeddingCacheHit` so repeated queries are easy to spot.

## Custom Terminologies

Custom terminologies are separate from imported systems. Create them with
`POST /terminologies`, then add records with a stable external `code`.

Custom record search text is built from:

- display
- description
- keywords

Arbitrary JSON metadata and connections are stored in the payload but are not
included in search text by default.

## Deployment and Usage

This server is designed to run within a private network without any public access.
Care or other systems can communicate with this server with its internal API key.
This server can be scaled up by adding more instances of the API worker.
