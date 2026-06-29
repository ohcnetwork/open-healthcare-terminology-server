# ICD-10-CM

ICD-10-CM is loaded from the CMS code descriptions tabular order ZIP into
`icd10cm_concept_document`.

ICD is a third-party terminology family published by WHO. ICD-10-CM source
data is distributed by United States government publishers. This project is not
endorsed by WHO, CMS, or NCHS. See [notices](../../NOTICE.md).

The loader stores the displayed dotted code, the raw CMS code, billable status,
short title, long title, and hierarchy inferred from code prefixes.

## Source Data

Default source:

```text
data/raw/icd10cm/april-1-2026-code-descriptions-tabular-order.zip
```

Download the default ICD release files:

```bash
pipenv run python -m ots.cli icd download
```

The ICD-10-CM loader expects `icd10cm_order_2026.txt` inside the ZIP.

## Load

Smoke test:

```bash
pipenv run python -m ots.cli icd load-10cm \
  --source data/raw/icd10cm/april-1-2026-code-descriptions-tabular-order.zip \
  --recreate \
  --limit 100
```

Full load:

```bash
pipenv run python -m ots.cli icd load-10cm \
  --source data/raw/icd10cm/april-1-2026-code-descriptions-tabular-order.zip \
  --recreate
```

## Row Shape

Each row includes:

- displayed dotted code in `payload.code`
- raw CMS code in `payload.rawCode`
- preferred long title
- optional short title as a synonym
- billable/category status
- parent, child, and ancestor IDs inferred from code prefixes
- `semantic_tag` set to `diagnosis`

## Search Text

Search text is built from:

- long title
- short title
- displayed dotted code
- raw code

## Embeddings

```bash
pipenv run python -m ots.cli common embed \
  --terminology icd10cm \
  --provider openai \
  --model text-embedding-3-large \
  --model-key openai:text-embedding-3-large:1536 \
  --dimensions 1536 \
  --batch-size 128 \
  --parallel-requests 4 \
  --recreate-index
```

## Query Examples

Lookup:

```bash
curl -H 'x-api-key: open-terminology-server-dev-key' \
  http://127.0.0.1:8000/terminologies/icd10cm/concepts/A00.0
```

Search:

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H 'x-api-key: open-terminology-server-dev-key' \
  -H 'Content-Type: application/json' \
  -d '{"terminology":"icd10cm", "query":"cholera", "mode":"lexical", "limit":10}'
```
