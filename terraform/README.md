# InstructaMate Atlas Flex (issue #34)

Provisions an Atlas **Flex** cluster in `AP_SOUTHEAST_2` (Sydney), a DB user with
`readWrite` on database `instructamate`, and PoC IP access `0.0.0.0/0`.

Does **not** create the Vector Search index — ingest code-ensures `chunks_vector`
from `src/instructamate/data/chunks_vector.json`.

## Prerequisites

- Existing Atlas project (org/project are out of scope)
- Atlas API keys as env vars: `MONGODB_ATLAS_PUBLIC_KEY` / `MONGODB_ATLAS_PRIVATE_KEY`
- Terraform >= 1.5

## Usage

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # set project_id
terraform init
terraform apply
terraform output -raw mongodb_uri   # → MONGODB_URI
```

Also set `VOYAGE_API_KEY` for explicit `voyage-4-lite` embeddings.
