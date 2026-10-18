# Plugin templates

Working, tested templates for each kind of TGO extension. To make your own:

1. **Copy** the closest template to your own module or package (e.g. `mytgo/tips.py`).
2. **Rename** the plugin: change `NAME` or the decorator's name string.
3. **Adapt**: `grep -n "TODO(adapt)" mytgo/tips.py` lists every spot you'll typically change.
4. **Run**: `python -m tgo run --input case --out out/case --plugin mytgo.tips`.

`tests/test_examples.py` runs all of them on the demo data, with the network blocked and on both engines. Copy
that test as a starting point for your own plugin's test.

| Template | Kind | What it does | Engines |
|---|---|---|---|
| [`csv_leads.py`](csv_leads.py) | source | reads `leads*.csv` (accounts and handles from a tip line or spreadsheet) into a `CaseGroup` | memory, duckdb |
| [`watchlist_extractor.py`](watchlist_extractor.py) | step after `extract` | tags messages, documents, OCR and transcripts with your watchlist terms; shows the **optional local AI model** pattern | memory (case slice on duckdb) |
| [`amplifier_step.py`](amplifier_step.py) | step after `aggregate` | ranks chats by how many other chats re-broadcast them | memory (case slice on duckdb) |
| [`amplifier_sql_step.py`](amplifier_sql_step.py) | step after `aggregate` | the same score in SQL over the full corpus, for millions of messages | duckdb |
| [`csv_exporter.py`](csv_exporter.py) | exporter | `csv/nodes.csv` and `csv/edges.csv` for Excel, Power BI or pandas; full-corpus `edges_full.csv` on duckdb | memory, duckdb |

Sample inputs are in [`sample_data/`](sample_data/): `leads.csv` and `watchlist.json`.

Try them all on the demo data:

```bash
python -m tgo demo --out out/demo                      # creates out/demo/input
cp examples/sample_data/leads.csv out/demo/input/
python -m tgo run --input out/demo/input --out out/demo-plugins \
  --plugin examples.csv_leads --plugin examples.watchlist_extractor \
  --plugin examples.amplifier_step --plugin examples.csv_exporter
```

Plugin settings go in your config file under `[plugins.options.<name>]`:

```toml
[plugins]
modules = ["examples.csv_leads", "examples.watchlist_extractor"]

[plugins.options.watchlist]
path = "config/watchlist.json"
model_dir = "models/my-local-classifier"   # optional, local only

[plugins.options.amplifiers]
min_sources = 3
```

The full guide, including changes that aren't plugins (new classes, relation types, validation rules and
viewer tweaks), is in [docs/extending.md](../docs/extending.md).
