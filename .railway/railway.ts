import { defineRailway, image, preserve, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const postgresVolume = volume("postgres-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "sfo", sizeMB: 5000 });
  const relayWorker = service("relay-worker", {
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start: "python -m relay.worker",
    replicas: { "sfo": 1 },
    deploy: { restartPolicyMaxRetries: 5 },
    env: { APP_MODE: preserve(), APP_ORIGIN: preserve(), DATABASE_URL: preserve(), JIRA_API_TOKEN: preserve(), JIRA_CONFIG_JSON: preserve(), JIRA_EMAIL: preserve(), OPENAI_API_KEY: preserve(), OPENAI_EMBEDDING_MODEL: preserve(), OPENAI_MAX_OUTPUT_TOKENS: preserve(), OPENAI_MODEL: preserve(), OPENAI_REASONING_EFFORT: preserve(), SESSION_SECRET: preserve() },
  });
  const relayWeb = service("relay-web", {
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start: "python -m relay.web",
    healthcheck: "/api/health",
    healthcheckTimeout: 120,
    preDeploy: "python -m relay.cli deploy-migrate",
    replicas: { "sfo": 1 },
    deploy: { restartPolicyMaxRetries: 5 },
    env: { APP_MODE: preserve(), APP_ORIGIN: preserve(), DATABASE_URL: preserve(), JIRA_API_TOKEN: preserve(), JIRA_CONFIG_JSON: preserve(), JIRA_EMAIL: preserve(), OPENAI_API_KEY: preserve(), OPENAI_EMBEDDING_MODEL: preserve(), OPENAI_MAX_OUTPUT_TOKENS: preserve(), OPENAI_MODEL: preserve(), OPENAI_REASONING_EFFORT: preserve(), PORT: preserve(), SESSION_SECRET: preserve() },
  });
  const Postgres = service("Postgres", {
    source: image("pgvector/pgvector:pg16"),
    replicas: { "sfo": 1 },
    networking: { privateNetworkEndpoint: "postgres" },
    volumeMounts: { "/var/lib/postgresql/data": postgresVolume },
    env: { DATABASE_URL: preserve(), PGDATA: preserve(), POSTGRES_DB: preserve(), POSTGRES_PASSWORD: preserve(), POSTGRES_USER: preserve() },
  });

  return project("Relay", {
    resources: [relayWorker, relayWeb, Postgres, postgresVolume],
  });
});
