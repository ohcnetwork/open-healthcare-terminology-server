# LOINC

LOINC is loaded from release CSV files into `loinc_concept_document`. The
original LOINC code is preserved in `payload.code`, exposed as `code` and
`display_code` in API responses, and indexed for lookup.

LOINC is a third-party registered trademark. This project is not endorsed by
Regenstrief Institute or the LOINC Committee. See [notices](../../NOTICE.md).

LOINC codes such as `4548-4` also have numeric internal `concept_id` values for
Postgres primary keys, hierarchy arrays, and embedding tables. Do not display
the internal `concept_id` as the LOINC code; use `code` or `display_code`.

## Source Data

The loader expects the unzipped LOINC release directory, for example:

```text
Loinc_2.82
```

It reads main LOINC names, definitions, related names, consumer names, parts,
groups, panel/form relationships, answer lists, map-to links, and linguistic
variants.

Rows with `STATUS=DISCOURAGED` are loaded but marked inactive. Default API
searches use `activeOnly=true`, so discouraged rows are ignored unless a request
explicitly asks for inactive content.

## Load

Smoke test:

```bash
pipenv run python -m ots.cli loinc load \
  --loinc-dir Loinc_2.82 \
  --recreate \
  --limit 100
```

Full load:

```bash
pipenv run python -m ots.cli loinc load \
  --loinc-dir Loinc_2.82 \
  --recreate
```

Clean existing rows in place without re-embedding:

```bash
pipenv run python -m ots.cli loinc cleanup-codes

pipenv run python -m ots.cli loinc cleanup-status
```

## Search Text

LOINC search text is English-only and intentionally strict for embedding
quality. It keeps clinical names, consumer names, English related names,
definitions, survey text, and example answers.

It excludes parts, groups, panel metadata, map metadata, internal IDs, URLs,
structural abbreviations, and linguistic variants.

Rebuild search text without re-importing the release:

```bash
pipenv run python -m ots.cli loinc rebuild-search-text --batch-size 5000
```

Use `--clear-embeddings` only when the changed text should invalidate existing
embedding rows.

## Embeddings

LOINC embedding population defaults to all active rows unless `--semantic-tags`
is provided.

```bash
pipenv run python -m ots.cli common embed \
  --terminology loinc \
  --provider openai \
  --model text-embedding-3-large \
  --model-key openai:text-embedding-3-large:1536 \
  --dimensions 1536 \
  --batch-size 128 \
  --parallel-requests 4 \
  --recreate-index
```

Ollama example:

```bash
pipenv run python -m ots.cli common embed \
  --terminology loinc \
  --provider ollama \
  --model embeddinggemma \
  --model-key ollama:embeddinggemma \
  --batch-size 64 \
  --recreate-index
```

## Query Examples

Lookup:

```bash
curl -H 'x-api-key: open-terminology-server-dev-key' \
  http://127.0.0.1:8000/terminologies/loinc/concepts/4548-4
```

Lexical search:

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H 'x-api-key: open-terminology-server-dev-key' \
  -H 'Content-Type: application/json' \
  -d '{"terminology":"loinc", "query":"heart rate", "mode":"lexical", "limit":10}'
```

Vector search:

```bash
curl -X POST http://127.0.0.1:8000/search/vector \
  -H 'x-api-key: open-terminology-server-dev-key' \
  -H 'Content-Type: application/json' \
  -d '{"terminology":"loinc", "query":"heart", "modelKey":"openai:text-embedding-3-large:1536", "limit":5, "detail":"basic"}'
```
