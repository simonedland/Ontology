# workspace/

Default host folders for the Docker stack (`compose.yaml`). Contents are git-ignored; only this
skeleton is tracked. Each folder can be moved with the variable in brackets (see `.env.example`).

| Folder | In container | Purpose |
|---|---|---|
| `data/` (`TGO_DATA`) | `/data` (read-only) | Desktop exports, MTProto collections, `analyst/judgements.json`; `collect` writes here |
| `out/` (`TGO_OUT`) | `/out` | run outputs, one sub-folder per case (`TGO_CASE`) |
| `config/` (`TGO_CONFIG_DIR`) | `/config` (read-only) | `tgo.toml` (copy `tgo.example.toml`) and/or `tgo.env` with `TGO_<SECTION>_<FIELD>=value` lines |
| `plugins/` (`TGO_PLUGIN_DIR`) | `/plugins` (read-only, on `PYTHONPATH`) | your plugin modules; enable with `TGO_PLUGINS=name1,name2` |
| `models/` (`TGO_MODELS`) | `/models` (read-only) | local ASR/OCR models (`whisper/`, `ocr/`) |
| `session/` (`TGO_SESSION_DIR`) | `/session` | Telethon session for `collect` (a credential: keep private) |
| `jdbc/` (`TGO_JDBC`) | Ontop `/opt/ontop/jdbc` | `duckdb_jdbc-<ver>.jar` |
| `neo4j-plugins/` (`TGO_NEO4J_PLUGINS`) | Neo4j `/plugins` | pre-fetched Neo4j plugin jars |
