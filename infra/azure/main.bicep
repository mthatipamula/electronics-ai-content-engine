// Azure deployment: Container Apps + managed identities + Key Vault + PostgreSQL (pgvector) + Service Bus.
// Azure OpenAI is an existing resource (created in the Foundry portal); this template only grants access to it.
//
// Security model
// - No API keys for Azure services: each app has its own user-assigned managed identity with only the roles it needs
//   (gateway -> Azure OpenAI; engine -> Service Bus send/receive; all -> AcrPull, Key Vault secrets).
// - Remaining secrets (database connection strings, service-to-service tokens) arrive as @secure parameters, are written
//   to Key Vault, and the container apps read them through their identity. They are never in app config or outputs.
// - Only the web app has public ingress; gateway, catalog MCP and engine are internal to the environment.

targetScope = 'resourceGroup'

@description('Short prefix for resource names, e.g. "ace-dev".')
@minLength(3)
@maxLength(12)
param prefix string

param location string = resourceGroup().location

@description('Name of the existing Azure OpenAI / Foundry resource (AIServices or OpenAI kind) in this resource group.')
param openAiAccountName string

@description('Azure OpenAI endpoint. Default: https://<openAiAccountName>.openai.azure.com/ (Foundry resources use their name as subdomain).')
param openAiEndpointOverride string = ''

param fastDeployment string = 'gpt-4.1-mini-1'
param qualityDeployment string = 'gpt-4.1-mini-1'
param embeddingDeployment string = 'text-embedding-3-small'

@description('Image tag to deploy (CI passes the git SHA).')
param imageTag string = 'latest'

@description('PostgreSQL admin password; generated once by deploy.sh and kept in a local git-ignored file.')
@secure()
param pgAdminPassword string

@description('Service-to-service tokens and gateway virtual keys (generated once by deploy.sh).')
@secure()
param appSecrets object

@description('False on the first run: creates the registry and data services so images can be pushed before apps start.')
param deployApps bool = true

@description('Optional Entra ID app registration (client ID) to require sign-in on the web app. Empty: no auth.')
param webAuthClientId string = ''

@description('Optional OTLP traces endpoint (e.g. an OpenTelemetry collector or Phoenix). Empty: tracing off.')
param otlpTracesEndpoint string = ''

var suffix = uniqueString(resourceGroup().id)
var apps = ['gateway', 'catalog', 'engine', 'web']

// Built-in role definition IDs
var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
  openAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  serviceBusSender: '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
  serviceBusReceiver: '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
}

// ---------------------------------------------------------------- observability

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

// ---------------------------------------------------------------- identities

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [for app in apps: {
  name: '${prefix}-${app}-id'
  location: location
}]

// ---------------------------------------------------------------- container registry

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: replace('${prefix}acr${suffix}', '-', '')
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (app, i) in apps: {
  name: guid(acr.id, app, roles.acrPull)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
    principalId: identity[i].properties.principalId
    principalType: 'ServicePrincipal'
  }
}]

// ---------------------------------------------------------------- Key Vault (RBAC)

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${prefix}-kv-${take(suffix, 6)}'
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

resource vaultRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (app, i) in apps: {
  name: guid(vault.id, app, roles.keyVaultSecretsUser)
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.keyVaultSecretsUser)
    principalId: identity[i].properties.principalId
    principalType: 'ServicePrincipal'
  }
}]

// ---------------------------------------------------------------- Azure OpenAI (existing): passwordless access

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: openAiAccountName
}

resource openAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAi.id, 'gateway', roles.openAiUser)
  scope: openAi
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.openAiUser)
    principalId: identity[0].properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------- Service Bus (job queue)

resource bus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: '${prefix}-sb-${take(suffix, 6)}'
  location: location
  sku: { name: 'Basic', tier: 'Basic' }
  properties: { disableLocalAuth: true } // identity only, no connection strings
}

resource queue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: bus
  name: 'content-jobs'
  properties: {
    maxDeliveryCount: 5               // then dead-lettered
    lockDuration: 'PT5M'
    deadLetteringOnMessageExpiration: true
  }
}

resource busRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in [roles.serviceBusSender, roles.serviceBusReceiver]: {
  name: guid(bus.id, 'engine', role)
  scope: bus
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
    principalId: identity[2].properties.principalId
    principalType: 'ServicePrincipal'
  }
}]

// ---------------------------------------------------------------- PostgreSQL Flexible Server + pgvector

resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2022-12-01' = {
  name: '${prefix}-pg-${take(suffix, 6)}'
  location: location
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: 'pgadmin'
    administratorLoginPassword: pgAdminPassword
    storage: { storageSizeGB: 32 }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
  }
}

resource pgVector 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2022-12-01' = {
  parent: pg
  name: 'azure.extensions'
  properties: { value: 'VECTOR', source: 'user-override' }
}

resource pgAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2022-12-01' = {
  parent: pg
  name: 'AllowAzureServices' // Container Apps egress; use VNet integration + private endpoint for production
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource databases 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2022-12-01' = [for db in ['gateway', 'catalog', 'content']: {
  parent: pg
  name: db
}]

// ---------------------------------------------------------------- Container Apps

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: logs.properties.customerId, sharedKey: logs.listKeys().primarySharedKey }
    }
  }
}

var pgHost = '${pg.name}.postgres.database.azure.com'
var dbUrl = 'postgresql://pgadmin:${uriComponent(pgAdminPassword)}@${pgHost}:5432'
var secretValues = {
  'db-url-gateway': '${dbUrl}/gateway?sslmode=require'
  'db-url-catalog': '${dbUrl}/catalog?sslmode=require'
  'db-url-content': '${dbUrl}/content?sslmode=require'
  'vk-content-engine': appSecrets.vkContentEngine
  'vk-catalog': appSecrets.vkCatalog
  'gateway-admin-token': appSecrets.gatewayAdminToken
  'gateway-virtual-keys': string([
    { app: 'content-engine', budget: 25, tiers: ['fast', 'quality'], key: appSecrets.vkContentEngine }
    { app: 'catalog-mcp', budget: 5, tiers: [], key: appSecrets.vkCatalog }
  ])
  'catalog-service-tokens': string({
    'content-engine': { token: appSecrets.catalogTokenEngine, scopes: ['catalog:read', 'drafts:write'] }
    'readonly-agent': { token: appSecrets.catalogTokenReadonly, scopes: ['catalog:read'] }
  })
  'catalog-token-engine': appSecrets.catalogTokenEngine
  'engine-service-tokens': string({
    'web-reviewer': { token: appSecrets.engineTokenWeb, scopes: ['content:read', 'content:request', 'content:review'] }
    evals: { token: appSecrets.engineTokenEvals, scopes: ['content:read', 'content:evaluate'] }
  })
  'engine-token-web': appSecrets.engineTokenWeb
}

var secretNames = ['db-url-gateway', 'db-url-catalog', 'db-url-content', 'vk-content-engine', 'vk-catalog'
  'gateway-admin-token', 'gateway-virtual-keys', 'catalog-service-tokens', 'catalog-token-engine', 'engine-service-tokens'
  'engine-token-web']

resource secrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = [for name in secretNames: {
  parent: vault
  name: name
  properties: { value: secretValues[name] }
}]

var otel = empty(otlpTracesEndpoint) ? [] : [{ name: 'OTEL_EXPORTER_OTLP_TRACES_ENDPOINT', value: otlpTracesEndpoint }]
var openAiEndpoint = empty(openAiEndpointOverride) ? 'https://${openAiAccountName}.openai.azure.com/' : openAiEndpointOverride

var specs = [
  {
    name: 'llm-gateway', id: 0, port: 8000, external: false
    command: ['uvicorn', 'gateway.main:app', '--host', '0.0.0.0', '--port', '8000']
    secrets: ['db-url-gateway', 'gateway-admin-token', 'gateway-virtual-keys']
    env: [
      { name: 'PORT', value: '8000' }
      { name: 'PROVIDER', value: 'azure' }
      { name: 'AZURE_OPENAI_ENDPOINT', value: openAiEndpoint }
      { name: 'AZURE_OPENAI_FAST_DEPLOYMENT', value: fastDeployment }
      { name: 'AZURE_OPENAI_QUALITY_DEPLOYMENT', value: qualityDeployment }
      { name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT', value: embeddingDeployment }
      { name: 'REDIS_URL', value: '' }
      { name: 'DATABASE_URL', secretRef: 'db-url-gateway' }
      { name: 'ADMIN_TOKEN', secretRef: 'gateway-admin-token' }
      { name: 'BOOTSTRAP_VIRTUAL_KEYS', secretRef: 'gateway-virtual-keys' }
    ]
  }
  {
    name: 'catalog-mcp', id: 1, port: 8001, external: false
    command: ['uvicorn', 'catalog.server:app', '--host', '0.0.0.0', '--port', '8001']
    secrets: ['db-url-catalog', 'vk-catalog', 'catalog-service-tokens']
    env: [
      { name: 'DATABASE_URL', secretRef: 'db-url-catalog' }
      { name: 'GATEWAY_URL', value: 'http://llm-gateway' }
      { name: 'GATEWAY_KEY', secretRef: 'vk-catalog' }
      { name: 'PUBLIC_URL', value: 'http://catalog-mcp' }
      { name: 'EMBEDDING_ID', value: 'azure:${embeddingDeployment}' }
      { name: 'SERVICE_TOKENS', secretRef: 'catalog-service-tokens' }
    ]
  }
  {
    name: 'content-engine', id: 2, port: 8002, external: false
    command: ['uvicorn', 'engine.api:app', '--host', '0.0.0.0', '--port', '8002']
    secrets: ['db-url-content', 'vk-content-engine', 'catalog-token-engine', 'engine-service-tokens']
    env: [
      { name: 'DATABASE_URL', secretRef: 'db-url-content' }
      { name: 'SERVICEBUS_NAMESPACE', value: '${bus.name}.servicebus.windows.net' }
      { name: 'GATEWAY_URL', value: 'http://llm-gateway' }
      { name: 'GATEWAY_KEY', secretRef: 'vk-content-engine' }
      { name: 'CATALOG_MCP_URL', value: 'http://catalog-mcp/mcp' }
      { name: 'CATALOG_TOKEN', secretRef: 'catalog-token-engine' }
      { name: 'SERVICE_TOKENS', secretRef: 'engine-service-tokens' }
    ]
  }
  {
    name: 'web', id: 3, port: 8080, external: true, command: []
    secrets: ['engine-token-web']
    env: [
      { name: 'ENGINE_URL', value: 'http://content-engine' }
      { name: 'ENGINE_TOKEN_WEB', secretRef: 'engine-token-web' }
    ]
  }
]

var appNames = ['llm-gateway', 'catalog-mcp', 'content-engine', 'web']

resource containerApps 'Microsoft.App/containerApps@2024-03-01' = [for (name, i) in appNames: if (deployApps) {
  name: name
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity[specs[i].id].id}': {} } }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: specs[i].external, targetPort: specs[i].port, transport: 'http', allowInsecure: !specs[i].external }
      registries: [{ server: acr.properties.loginServer, identity: identity[specs[i].id].id }]
      secrets: [for secretName in specs[i].secrets: { name: secretName, keyVaultUrl: '${vault.properties.vaultUri}secrets/${secretName}', identity: identity[specs[i].id].id }]
    }
    template: {
      containers: [{
        name: specs[i].name
        image: '${acr.properties.loginServer}/${specs[i].name}:${imageTag}'
        command: empty(specs[i].command) ? null : specs[i].command
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: concat(specs[i].env, otel, [{ name: 'AZURE_CLIENT_ID', value: identity[specs[i].id].properties.clientId }])
        probes: [
          { type: 'Readiness', httpGet: { path: specs[i].name == 'web' ? '/' : '/healthz', port: specs[i].port }, periodSeconds: 10 }
          { type: 'Liveness', httpGet: { path: specs[i].name == 'web' ? '/' : '/healthz', port: specs[i].port }, periodSeconds: 30 }
        ]
      }]
      scale: { minReplicas: specs[i].name == 'content-engine' ? 1 : 0, maxReplicas: specs[i].name == 'web' ? 2 : 1 }
    }
  }
  dependsOn: [acrPull, vaultRead, databases, secrets, pgVector, pgAzure]
}]

resource webAuth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = if (deployApps && !empty(webAuthClientId)) {
  name: 'web/current'
  properties: {
    platform: { enabled: true }
    globalValidation: { unauthenticatedClientAction: 'RedirectToLoginPage' }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: webAuthClientId
          openIdIssuer: '${environment().authentication.loginEndpoint}${subscription().tenantId}/v2.0'
        }
      }
    }
  }
  dependsOn: [containerApps]
}

output registry string = acr.properties.loginServer
output keyVault string = vault.name
output postgresServer string = pg.name
output webUrl string = deployApps ? 'https://${containerApps[3]!.properties.configuration.ingress.fqdn}' : ''
