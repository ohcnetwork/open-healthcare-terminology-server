# ICD-11 MMS

ICD-11 MMS is loaded from the WHO simple tabulation ZIP into
`icd11_concept_document`.

ICD is a third-party terminology family published by WHO. This project is not
endorsed by WHO. See [notices](../../NOTICE.md).

The loader stores categories, blocks, chapters, titles, coding notes, browser
links, class kind, primary tabulation status, and hierarchy from the tabulation
parent URI.

## Source Data

Default source:

```text
data/raw/icd11/SimpleTabulation-ICD-11-MMS-en-2026-01.zip
```

Download the default ICD release files:

```bash
pipenv run python -m ots.cli icd download
```

The ICD-11 loader expects `SimpleTabulation-ICD-11-MMS-en.txt` inside the ZIP.

## Load

Smoke test:

```bash
pipenv run python -m ots.cli icd load-11 \
  --source data/raw/icd11/SimpleTabulation-ICD-11-MMS-en-2026-01.zip \
  --recreate \
  --limit 100
```

Full load:

```bash
pipenv run python -m ots.cli icd load-11 \
  --source data/raw/icd11/SimpleTabulation-ICD-11-MMS-en-2026-01.zip \
  --recreate
```

## Row Shape

Each row includes:

- ICD code, block ID, or chapter identifier
- external row identity
- title and optional coding note
- class kind and depth
- browser link and WHO URIs
- parent, child, and ancestor IDs from tabulation parent links
- `semantic_tag` set from class kind, with categories mapped to `diagnosis`

## Search Text

Search text is built from:

- title
- coding note
- ICD code
- block ID
- grouping values

## Embeddings

```bash
pipenv run python -m ots.cli common embed \
  --terminology icd11 \
  --provider openai \
  --model text-embedding-3-large \
  --model-key openai:text-embedding-3-large:1536 \
  --dimensions 1536 \
  --batch-size 128 \
  --parallel-requests 4 \
  --recreate-index
```

## Query Examples

Lexical search:

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H 'x-api-key: open-terminology-server-dev-key' \
  -H 'Content-Type: application/json' \
  -d '{"terminology":"icd11", "query":"diabetes", "mode":"lexical", "limit":10}'
```

Vector search:

```bash
curl -X POST http://127.0.0.1:8000/search/vector \
  -H 'x-api-key: open-terminology-server-dev-key' \
  -H 'Content-Type: application/json' \
  -d '{"terminology":"icd11", "query":"diabetes", "modelKey":"openai:text-embedding-3-large:1536", "limit":10}'
```
