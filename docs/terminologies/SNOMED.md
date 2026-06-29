# SNOMED CT

SNOMED CT is loaded from RF2 Snapshot files into `snomed_concept_document`.
The loader is optimized for read-heavy search and lookup, not authoring.

For RF2 file details, see [SNOMED RF2 reference](../SNOMED_RF2_REFERENCE.md).

## Source Data

Use the `Snapshot` folder from the SNOMED CT International RF2 release:

```text
SnomedCT_InternationalRF2_PRODUCTION_20260601T120000Z/Snapshot
```

The loader reads:

- concepts
- descriptions
- language refsets
- text definitions
- inferred relationships
- optional map, association, refset, attribute, and concrete value files

## Load

Smoke test:

```bash
pipenv run python -m ots.cli snomed load \
  --rf2-dir SnomedCT_InternationalRF2_PRODUCTION_20260601T120000Z/Snapshot \
  --recreate \
  --limit 1000
```

Full load:

```bash
pipenv run python -m ots.cli snomed load \
  --rf2-dir SnomedCT_InternationalRF2_PRODUCTION_20260601T120000Z/Snapshot \
  --recreate
```

Use `--skip-optional` to load only concepts, descriptions, language refset,
text definitions, and inferred relationships.

## Editions And Extensions

Use `ots.cli snomed load-packages` when a queryable edition is made from
multiple RF2 zip packages, such as SNOMED CT India plus AYUSH, drug, language,
geography, COVID-19, and reference-set packages.

Preview the import grouping:

```bash
pipenv run python -m ots.cli snomed load-packages \
  --source-dir data/raw \
  --edition-version snomed_india_20260313 \
  --plan-only
```

Load all discovered India packages into a composed edition backed by an existing
International/core edition:

```bash
pipenv run python -m ots.cli snomed load-packages \
  --source-dir data/raw \
  --edition-version snomed_india_20260313 \
  --base-version 20260601 \
  --default-version
```

The script extracts zip files into `data/imports/snomed_rf2`, registers every zip
as a release package, links the package to the target edition, and upserts into a
single edition table. This keeps each SCTID as one row per edition while still
recording which release package supplied the data.

Load only one package group or package name:

```bash
pipenv run python -m ots.cli snomed load-packages \
  --source-dir data/raw \
  --edition-version snomed_india_20260313 \
  --base-version 20260601 \
  --include-package drug
```

Use `--recreate-edition` to drop and rebuild the target edition table before
resyncing/loading. Use `--force-extract` only when the extracted RF2 files should
be refreshed from the zip files.

## Row Shape

Each concept row contains:

- core concept fields
- FSN, preferred term, semantic tag, synonyms, and text definitions
- active descriptions with language acceptability
- direct parent IDs, direct child IDs, and precomputed ancestor IDs
- inferred non-`is-a` relationships
- optional maps, associations, refsets, attribute values, and concrete values
- `search_text` and generated Postgres `search_vector`
- JSON `payload`

## Search Text

SNOMED search text is embedding-oriented. It removes raw SCTIDs, semantic tags,
relationship type labels, map codes, and top-level category names while keeping
clinical names, synonyms, definitions, selected parent terms, and relationship
destination terms.

Rebuild search text without re-importing RF2:

```bash
pipenv run python -m ots.cli snomed rebuild-search-text --batch-size 5000
```

Use `--clear-embeddings` only when the changed text should invalidate existing
embedding rows.

## Embeddings

By default, SNOMED embedding population is limited to concepts whose
`semantic_tag` is `disorder` or `finding`.

```bash
pipenv run python -m ots.cli common embed \
  --terminology snomed \
  --provider openai \
  --model text-embedding-3-large \
  --model-key openai:text-embedding-3-large:1536 \
  --dimensions 1536 \
  --batch-size 128 \
  --parallel-requests 4 \
  --recreate-index
```

To embed every active SNOMED concept type:

```bash
pipenv run python -m ots.cli common embed \
  --terminology snomed \
  --provider openai \
  --model text-embedding-3-large \
  --model-key openai:text-embedding-3-large:1536 \
  --dimensions 1536 \
  --all-semantic-tags \
  --batch-size 128 \
  --parallel-requests 4 \
  --recreate-index
```

## Query Examples

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H 'x-api-key: open-terminology-server-dev-key' \
  -H 'Content-Type: application/json' \
  -d '{"terminology":"snomed", "query":"heart failure", "mode":"lexical", "limit":10}'
```

```bash
curl -X POST http://127.0.0.1:8000/search/vector \
  -H 'x-api-key: open-terminology-server-dev-key' \
  -H 'Content-Type: application/json' \
  -d '{"terminology":"snomed", "query":"heart failure", "modelKey":"openai:text-embedding-3-large:1536", "limit":10}'
```

Scoped disease/finding search:

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H 'x-api-key: open-terminology-server-dev-key' \
  -H 'Content-Type: application/json' \
  -d '{"terminology":"snomed", "query":"heart", "ancestorConceptId":404684003, "includeAncestor":false, "semanticTags":["disorder"], "limit":10}'
```
