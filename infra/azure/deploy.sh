#!/usr/bin/env bash
# Deploys to Azure in two phases (infrastructure, then images, then apps). Creates billable resources: run it yourself.
#
#   RESOURCE_GROUP=azure-signup-rg OPENAI_ACCOUNT=mthatipamula-0044-resource PREFIX=ace-dev ./infra/azure/deploy.sh
#
# Requires: az login, and the resource group that already holds your Azure OpenAI / Foundry resource.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${RESOURCE_GROUP:?set RESOURCE_GROUP}" "${OPENAI_ACCOUNT:?set OPENAI_ACCOUNT}"
PREFIX="${PREFIX:-ace-dev}"
TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD)}"
SECRETS=.azure/secrets.env

# Secrets are generated once, kept in a git-ignored file, and passed as @secure parameters (never printed).
# CI passes them as environment variables instead (repository secrets).
if [[ -z "${PG_ADMIN_PASSWORD:-}" && ! -f $SECRETS ]]; then
  mkdir -p .azure && umask 077
  gen() { openssl rand -base64 30 | tr -dc 'A-Za-z0-9' | head -c 32; }
  cat > $SECRETS <<ENV
PG_ADMIN_PASSWORD=Pg1-$(gen)
VK_CONTENT_ENGINE=vk-$(gen)
VK_CATALOG=vk-$(gen)
GATEWAY_ADMIN_TOKEN=$(gen)
CATALOG_TOKEN_ENGINE=$(gen)
CATALOG_TOKEN_READONLY=$(gen)
ENGINE_TOKEN_WEB=$(gen)
ENGINE_TOKEN_EVALS=$(gen)
ENV
  echo "Generated $SECRETS"
fi
if [[ -z "${PG_ADMIN_PASSWORD:-}" ]]; then set -a; . $SECRETS; set +a; fi
APP_SECRETS=$(printf '{"vkContentEngine":"%s","vkCatalog":"%s","gatewayAdminToken":"%s","catalogTokenEngine":"%s","catalogTokenReadonly":"%s","engineTokenWeb":"%s","engineTokenEvals":"%s"}' \
  "$VK_CONTENT_ENGINE" "$VK_CATALOG" "$GATEWAY_ADMIN_TOKEN" "$CATALOG_TOKEN_ENGINE" "$CATALOG_TOKEN_READONLY" "$ENGINE_TOKEN_WEB" "$ENGINE_TOKEN_EVALS")

deploy() {
  az deployment group create -g "$RESOURCE_GROUP" -n "content-engine-$1" --template-file infra/azure/main.bicep \
    --parameters prefix="$PREFIX" openAiAccountName="$OPENAI_ACCOUNT" imageTag="$TAG" deployApps="$2" \
      pgAdminPassword="$PG_ADMIN_PASSWORD" appSecrets="$APP_SECRETS" \
    --query properties.outputs -o json
}

echo "1/3 infrastructure"
OUT=$(deploy infra false)
REGISTRY=$(echo "$OUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['registry']['value'])")

echo "2/3 images -> $REGISTRY (built in Azure with ACR Tasks)"
for svc in llm-gateway catalog-mcp content-engine; do
  az acr build -r "${REGISTRY%%.*}" -t "$svc:$TAG" --build-arg SERVICE=$svc . --no-logs -o none
done
az acr build -r "${REGISTRY%%.*}" -t "web:$TAG" web --no-logs -o none

echo "3/3 apps"
deploy apps true | python3 -c "import json,sys; print('Web app:', json.load(sys.stdin)['webUrl']['value'])"
