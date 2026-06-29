# Architecture

The server now has three explicit layers:

- `ots/api/schemas.py`: Pydantic request/response contracts.
- `ots/api/views/`: Starlette view functions grouped by API area.
- `ots/api/routes.py`: route registration.
- `ots/api/auth.py`: fixed API-key middleware.
- `ots/api/openapi.py`: OpenAPI JSON and Swagger UI.
- `ots/config.py`: environment-driven Django-style uppercase settings constants.
- `ots/db/models.py`: SQLAlchemy models for registry tables and Core table factories.
- `ots/db/schema.py`: Postgres/pgvector schema and index creation helpers.
- `ots/terminology/base.py`: common terminology model helpers and custom terminology support.
- `ots/terminology/registry.py`: central registry for built-in terminology systems.
- `ots/terminology/<system>/model.py`: terminology-specific model behavior.
- `ots/terminology/<system>/scripts/`: terminology-specific import and maintenance commands.
- `ots/terminologies.py`: compatibility shim for older imports.
- `ots/embedding_providers/base.py`: base embedding provider contract.
- `ots/embedding_providers/<provider>.py`: provider-specific SDK behavior and defaults.
- `ots/embedding_providers/registry.py`: central registry for embedding providers.

Built-in terminology definitions are registered from `ots/terminology/registry.py`.
Adding another imported terminology should normally mean adding
`ots/terminology/<key>/model.py`, putting its loader commands under
`ots/terminology/<key>/scripts/`, and registering its `TERMINOLOGY` instance in
the central registry.

Embedding providers follow the same pattern. Provider-specific SDK arguments are
owned by each provider and accepted as a JSON object named `providerOptions` in
API requests or `--provider-options` in the CLI. The embedding population flow
keeps shared values such as terminology, version, model, dimensions, storage,
batch size, and parallelism provider-neutral.

Terminology-specific loader behavior is documented separately:

- [SNOMED CT](terminologies/SNOMED.md)
- [LOINC](terminologies/LOINC.md)
- [ICD-10-CM](terminologies/ICD10CM.md)
- [ICD-11 MMS](terminologies/ICD11.md)

The terminology registry is modeled with SQLAlchemy:

- `terminology_system`
- `terminology_version`
- `terminology_release_package`
- `terminology_edition_package`
- `embedding_model`

Concept documents remain one physical table per terminology, for example:

- `snomed_concept_document`
- `loinc_concept_document`
- `icd10cm_concept_document`
- `icd11_concept_document`
- `{custom_key}_concept_document`

Those concept tables are still queried with focused SQL because lexical search,
ancestor filters, and pgvector ranking need careful control over SQL shape and
indexes.

Custom terminologies are distinct from imported terminologies:

- imported: SNOMED and LOINC, loaded from source releases and read-only through the API.
- imported: ICD-10-CM and ICD-11 MMS, loaded from official CMS/WHO release files and read-only through the API.
- custom: editable code systems created through `POST /terminologies`.

Custom records use the external `code` as their stable identifier. Internally, the concept table still needs a bigint `concept_id`, so custom systems derive a deterministic bigint from `terminology_key + code`.

Search text for custom records is intentionally clean:

- display
- description
- keywords

Arbitrary JSON metadata is stored in `payload.metadata`, but it is not included in search text by default.

## API Key

The current development auth wall uses a fixed API key:

```text
x-api-key: open-terminology-server-dev-key
```

Swagger UI is available at `/docs`. The docs and `/openapi.json` are public so the browser can load the schema, but API operations require the key.
