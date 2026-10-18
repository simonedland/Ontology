# Running TGO in Docker

The whole stack runs from one image and one [`compose.yaml`](../compose.yaml): the pipeline, the static
viewer, the live dashboard, live collection, the test suite, and the optional Neo4j and Ontop serving
tiers. Every piece is a compose profile, so nothing starts unless you ask for it.

## Quick start

```bash
cp .env.example .env              # optional: every setting has a default
docker compose build              # builds tgo:local with the [scale] extra
docker compose run --rm demo      # synthetic case -> workspace/out/demo (report.html, dashboard.html)
docker compose --profile viewer up -d   # browse workspace/out at http://127.0.0.1:8000
```

Put your own data (Desktop exports, `tgo collect` output, `analyst/judgements.json`) in `workspace/data/`,
then run:

```bash
docker compose run --rm pipeline                                  # -> workspace/out/case
TGO_CASE=acme TGO_RUN_ARGS="--engine duckdb" docker compose run --rm pipeline
docker compose run --rm tgo dashboard --out /out/acme             # any tgo sub-command
```

Git Bash on Windows rewrites `/out`-style arguments into Windows paths. Run `export MSYS_NO_PATHCONV=1`
first, or use PowerShell.

## Services

| Run | What it does | Network |
|---|---|---|
| `docker compose run --rm tgo <cmd>` | the `tgo` CLI (`demo`, `run`, `dashboard`, `ontop`, `config`, `offline-check`, ...) | none |
| `docker compose run --rm demo` | `tgo demo --dashboard --out /out/demo` | none |
| `docker compose run --rm pipeline` | `tgo run --input /data/$TGO_INPUT --out /out/$TGO_CASE --dashboard $TGO_RUN_ARGS` | none |
| `docker compose run --rm test` | the repository test suite inside the image (`dev` target) | none |
| `docker compose --profile viewer up -d` | static file server over `workspace/out` on :8000 | internal |
| `docker compose --profile live up -d` | `tgo serve`: watches `/data/$TGO_INPUT`, ingests new data and pushes dashboard updates, on :8765 | internal |
| `docker compose run --rm collect --target @chan --out /data/mtproto` | live Telethon collection into `workspace/data` | **internet** |
| `docker compose --profile neo4j up -d` | bulk-imports `out/$TGO_CASE/neo4j` (from a `--engine duckdb` run), then serves Neo4j on :7474 / :7687 | internal |
| `docker compose --profile ontop up -d` | SPARQL over `out/$TGO_CASE/tgo.duckdb` on :8080 | internal |

The live dashboard needs a token when it is reached through the proxy. Set `TGO_TOKEN` in `.env`, or read
the generated one with `docker compose logs live`, then open `http://127.0.0.1:8765/?token=<token>`.

Neo4j refuses to start until `NEO4J_PASSWORD` (8+ characters) is set. Every `up` rebuilds the database from
the run's CSVs, because it is a disposable view of the DuckDB store. The password, though, is fixed when the
`neo4j_data` volume is first created. To change it later, run `docker compose --profile neo4j down -v`
first; this deletes only the Neo4j view, which the next `up` rebuilds. For Ontop, first write the mapping
with `docker compose run --rm tgo ontop --out /out/<case>`, then put `duckdb_jdbc-<ver>.jar` (matching the
duckdb wheel in the image) in `workspace/jdbc/`.

## Network model

The offline guarantee is enforced by the container runtime, not just by code review:

- **Pipeline-type services** (`tgo`, `demo`, `pipeline`, `test`) run with `network_mode: none`. They have no
  interface besides loopback, so native code (DuckDB, ONNX, ...) cannot reach anything either.
- **Servers** (`viewer`, `live`, `neo4j`, `ontop`) sit on `backend`, an `internal: true` network. It has no
  egress, and Docker will not publish ports from it.
- **`proxy`** is the only container that publishes ports. It joins `backend` plus an edge bridge and
  forwards raw TCP for the routes in `TGO_PROXY_ROUTES`. Compose binds it to `TGO_BIND` (127.0.0.1 by
  default). It is about 80 lines of stdlib code in `docker/proxy.py`.
- **`collect`** is the only service with internet access. It is the only image with Telethon installed (the
  `-collect` tag).

The TGO services also run as a non-root fixed UID, with a read-only root filesystem (tmpfs `/tmp` and
`$HOME`), all capabilities dropped, `no-new-privileges`, and an init process. The image bakes the
offline kill switches from `tgo/_env.py` plus `PIP_NO_INDEX=1`, and its build fails if
`tgo offline-check` does.

## Shaping it

Change things in this order, from least to most invasive:

1. **`.env`** (copied from [`.env.example`](../.env.example)): folders, image extras, CPU and memory
   limits, ports, case name, extra `tgo run` flags, plugin list, model paths. Host-side variable names
   avoid the `TGO_<SECTION>_` prefixes on purpose. Inside a container those names are config overrides,
   and an unknown field raises an error.
2. **`workspace/config/`**: `tgo.toml` (copy [`tgo.example.toml`](../workspace/config/tgo.example.toml)) for
   any setting in [configuration.md](configuration.md), and/or `tgo.env` with `TGO_<SECTION>_<FIELD>=value`
   lines (e.g. `TGO_COORDINATION_TIME_WINDOW=120`). Precedence is defaults, then `tgo.toml`, then env.
3. **`workspace/plugins/`**: drop a plugin module in (see [extending.md](extending.md) and
   [examples/](../examples/README.md)) and enable it with `TGO_PLUGINS=my_plugin`. The folder is on
   `PYTHONPATH`. To bake a packaged plugin into the image instead, use `TGO_PIP_EXTRA="my-tgo-plugin==1.2"`.
4. **Image build args**: `TGO_EXTRAS=all` or `scale,docs,ids` for more extractors. Use
   `TGO_APT_PACKAGES="tesseract-ocr tesseract-ocr-eng"` for system packages, and
   `docker build --build-arg PYTHON_IMAGE=...` for another base image.
5. **`compose.override.yaml`** (auto-loaded) or `-f compose.yaml -f extra.yaml`: add services, mounts or
   networks. Re-use the `x-tgo` defaults with `<<: *tgo` only inside `compose.yaml` itself; in an override
   file, use `extends: {file: compose.yaml, service: tgo}`.

### Container resources

A container hides its CPU and memory limits from `os.cpu_count()` and from DuckDB's auto-detection, so the
compose file sizes the engine explicitly. Set `TGO_CPUS` and `TGO_MEMORY` for the container. Set
`TGO_SCALE_WORKERS` to about CPUs - 1, `TGO_SCALE_THREADS` to about the CPU count, and
`TGO_SCALE_MEMORY_LIMIT` below `TGO_MEMORY` (allow about 0.4 GB per worker). DuckDB spills to the `spill`
named volume (`/spill`). A 10M-message run spills about 10 GB. The named volume is much faster than a
Windows bind mount. N-Triples export is off by default (`TGO_SCALE_EXPORTS`) because it is slow and
multi-GB at that size.

## Air-gapped hosts

On a connected machine:

```bash
docker compose build                                   # tgo:local
docker build --target wheelhouse --output type=local,dest=wheelhouse .   # optional: wheels for non-Docker installs
docker save -o tgo.tar tgo:local neo4j:5.26.32-community ontop/ontop    # the latter two only if you use them
```

On the target, run `docker load -i tgo.tar` and then use compose as usual. Nothing in the running stack
downloads anything, except `collect`, which by definition does.

## CI

The `docker` job in [ci.yml](../ci.yml) builds the image, runs the demo and the DuckDB
engine under `network_mode: none` with a read-only root, and runs the test suite in the `dev` image.
