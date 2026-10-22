# Deploying to Azure

> **Cost.** This creates billable resources. Approximate monthly cost when idle-to-light use: Container Apps
> (consumption, content-engine kept at 1 replica) ~$15–30, PostgreSQL Flexible Server B1ms ~$15–20, Container Registry
> Basic ~$5, Service Bus Basic and Key Vault cents, Log Analytics a few dollars. Azure OpenAI is pay-per-token.
> Delete the resources when done (below).

## What gets created

| Resource | Purpose | Security |
|---|---|---|
| Container Apps environment + 4 apps | gateway, catalog MCP, engine (internal ingress), web (public) | one user-assigned managed identity per app |
| Key Vault (RBAC) | connection strings, service tokens, gateway virtual keys | apps read their own secrets via managed identity |
| PostgreSQL Flexible Server (+ `vector` extension) | gateway ledger, catalog + embeddings, jobs | TLS required; Azure-services firewall rule (use VNet + private endpoint for production) |
| Service Bus (Basic) queue `content-jobs` | job queue with dead-lettering | local auth disabled: identity only |
| Container Registry | images | AcrPull via managed identity, admin user off |
| Role on your existing Azure OpenAI resource | `Cognitive Services OpenAI User` for the gateway identity | no API key in Azure |

## Option A: deploy from your machine

Requires the Azure CLI (`brew install azure-cli`) and `az login`. Use the resource group that already contains your
Azure OpenAI / Foundry resource.

```bash
RESOURCE_GROUP=azure-signup-rg OPENAI_ACCOUNT=mthatipamula-0044-resource PREFIX=ace-dev ./infra/azure/deploy.sh
```

The script generates secrets once into `.azure/secrets.env` (git-ignored, never printed), deploys the
infrastructure, builds the images in Azure with ACR Tasks, then deploys the apps and prints the web URL.

To require Entra ID sign-in on the web app, create an app registration and pass `webAuthClientId` (see
`main.bicep`).

## Option B: GitHub Actions (`.github/workflows/deploy.yml`)

1. Create an app registration for GitHub, add a **federated credential** for
   `repo:<owner>/electronics-ai-content-engine:environment:dev`, and give it **Contributor** and
   **User Access Administrator** (role assignments) on the resource group. No client secret is created.
2. Repository variables: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `RESOURCE_GROUP`,
   `OPENAI_ACCOUNT`.
3. Repository secrets (generate random values once): `PG_ADMIN_PASSWORD`, `VK_CONTENT_ENGINE`, `VK_CATALOG`,
   `GATEWAY_ADMIN_TOKEN`, `CATALOG_TOKEN_ENGINE`, `CATALOG_TOKEN_READONLY`, `ENGINE_TOKEN_WEB`, `ENGINE_TOKEN_EVALS`.
4. Run **Deploy to Azure** from the Actions tab.

## Clean up

```bash
for app in web content-engine catalog-mcp llm-gateway; do az containerapp delete -g $RESOURCE_GROUP -n $app -y; done
```

then delete the `ace-dev-*` resources in the portal (or the whole resource group, if it holds nothing else you need).

## Production hardening not in this template

- VNet-integrated environment with private endpoints for PostgreSQL, Key Vault and Service Bus.
- Entra ID authentication to PostgreSQL per service identity instead of one admin login.
- Entra ID tokens between services instead of shared bearer tokens.
- Azure Managed Redis for the gateway cache and rate limits when running more than one gateway replica.
- GitOps (Argo CD / Flux) if the platform moves to AKS.
