#!/usr/bin/env bash
# Creates .env with random local secrets (never printed). Azure OpenAI settings are left for you to fill in.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f .env ]]; then echo ".env already exists; leaving it alone."; exit 0; fi
gen() { openssl rand -base64 30 | tr -dc 'A-Za-z0-9' | head -c 32; }
umask 077
cat > .env <<ENV
# --- Azure OpenAI (fill these in; leave LLM_PROVIDER=mock to run without a model) ---
LLM_PROVIDER=mock
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_FAST_DEPLOYMENT=gpt-4.1-mini
AZURE_OPENAI_QUALITY_DEPLOYMENT=gpt-4.1
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small

# --- generated local secrets ---
POSTGRES_PASSWORD=$(gen)
DB_PASSWORD_GATEWAY=$(gen)
DB_PASSWORD_CATALOG=$(gen)
DB_PASSWORD_CONTENT=$(gen)
GATEWAY_ADMIN_TOKEN=$(gen)
VK_CONTENT_ENGINE=vk-$(gen)
VK_CATALOG=vk-$(gen)
CATALOG_TOKEN_ENGINE=$(gen)
CATALOG_TOKEN_READONLY=$(gen)
ENGINE_TOKEN_WEB=$(gen)
ENGINE_TOKEN_EVALS=$(gen)
ENV
echo "Wrote .env. Add your Azure OpenAI endpoint and key, then set LLM_PROVIDER=azure."
