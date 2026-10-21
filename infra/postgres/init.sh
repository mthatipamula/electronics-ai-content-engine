#!/bin/bash
# One database and owner per service, all with pgvector available.
set -euo pipefail
for svc in gateway catalog content; do
  var="DB_PASSWORD_${svc^^}"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<SQL
CREATE USER $svc WITH PASSWORD '${!var}';
CREATE DATABASE $svc OWNER $svc;
\c $svc
CREATE EXTENSION IF NOT EXISTS vector;
SQL
done
