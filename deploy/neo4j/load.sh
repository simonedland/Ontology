#!/bin/sh
# Bulk-load a TGO duckdb-engine output into the Neo4j serving tier (replaces the database), then start it.
set -eu
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: keep container paths like /import intact
cd "$(dirname "$0")"
: "${TGO_OUT:?set TGO_OUT to the tgo run --engine duckdb output dir}"
docker compose down >/dev/null 2>&1 || true
docker compose run --rm --no-deps neo4j neo4j-admin database import full neo4j --overwrite-destination \
  --id-type=integer --array-delimiter=";" --nodes=/import/nodes.csv --relationships=/import/relationships.csv
docker compose up -d
echo "Neo4j: http://127.0.0.1:7474  (bolt://127.0.0.1:7687)"
