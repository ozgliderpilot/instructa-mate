# InstructaMate Atlas Flex (issue #34)

Provisions an Atlas **Flex** cluster in `AP_SOUTHEAST_2` (Sydney), a DB user with
`readWrite` on database `instructamate`, and PoC IP access `0.0.0.0/0`.

Does **not** create the Vector Search index — ingest code-ensures `chunks_vector`
from `src/instructamate/data/chunks_vector.json`.

## Prerequisites

- Existing Atlas project (org/project are out of scope)
- Repo `.env` with:
  - `MONGODB_ATLAS_PUBLIC_KEY` / `MONGODB_ATLAS_PRIVATE_KEY`
  - `MONGODB_PROJECT_ID` (existing Atlas project id)
- Terraform >= 1.5

No `terraform.tfvars` — `./tf.sh` maps `MONGODB_PROJECT_ID` → `TF_VAR_project_id`.

## Usage

```bash
cd terraform
./tf.sh init
./tf.sh apply
./tf.sh output -raw mongodb_uri   # → MONGODB_URI in .env
```

Also set `VOYAGE_API_KEY` for explicit `voyage-4-lite` embeddings.
