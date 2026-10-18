# Data dashboard

`dashboard.html` is an interactive overview of everything in a TGO output folder. It is one self-contained
file that works offline from `file://`, with Apache ECharts and the data inlined. Where `report.html` is the
investigation view for following evidence edge by edge, the dashboard answers *what is in this store and
how does it hang together*.

```bash
python -m tgo run --input case --out out/case --dashboard      # build it as part of a run
python -m tgo dashboard --out out/case --open                  # or for any existing output folder
python -m tgo demo --dashboard --open                          # try it on the fictional demo case
```

It works with both engines. With `--engine duckdb`, totals, flows and activity come from SQL over the whole
corpus, and the relation network and schema graph show the bounded case slice. The page says so under the
filters.

## What's on it

| Section | Panel | Shows |
|---|---|---|
| Header | stat tiles | distinct relations, nodes, observations, the share of derived facts, accounts, chats, messages, media, identifiers, inferred groups, person clusters, guardrail status |
| Relations | **relation network** | actors (accounts, chats, communities, inferred groups, person clusters, identifiers, devices). Message- and media-level facts are *projected* onto the chat or account carrying them, so e.g. a wallet in a voice note links to its chat. Colour + shape = entity family; line style = epistemic status; opacity = confidence; width = observation count. Click a node for its properties and every relation; click a relation in the panel to walk the graph. |
| | relation flows (Sankey) | source family → relation group → target family |
| | family interplay (chord) | how strongly each kind of entity is tied to the others |
| Ontology | schema graph | the classes present and the relation types between them, in VOWL notation (classes as circles sized by instances, labelled relations, dashed `subClassOf`) |
| | class hierarchy (sunburst) | instances per class across the six TGO layers; *Unused classes* shows the whole vocabulary |
| Activity | messages per day | top chats plus "Other". Drag the handles to set a time window, which also filters the network. |
| | weekly rhythm | messages by weekday × hour (UTC) |
| Evidence quality | status by relation group | observed / inferred / file-declared / analyst per group |
| | confidence of derived facts | histogram of confidence for every non-observed edge |
| Findings | inferred groups, identity candidates, guardrails, top identifiers, busiest chats | click a row to focus it in the network |

**Global filters** above the charts apply to every relation chart: evidence status, minimum confidence,
relation groups (provenance is off by default) and search. Every chart has a **Table** button with the same
numbers as an accessible table. The theme follows the OS, and the **Theme** button switches it.
`dashboard.html#/node/<key>` opens with that node selected. When `report.html` sits next to it, the details
panel links to the matching investigation view.

## Scale behaviour

The browser never receives the corpus, only compact pre-aggregated tables.

- **Network size:** the relation network keeps the `--max-nodes` (default 500) actors with the highest
  weighted degree. Inferred groups, person clusters and communities are always kept.
- **Link cap:** it draws at most 4 × that many relations. Every node keeps its strongest relation; derived
  structure comes before the heaviest observed relations.
- **Large groups:** an inferred group with more than 40 members shows its strongest 40. The full membership
  is in the findings table and the store.
- **Layout:** positions are computed in Python (seeded, so stable across runs and filters). Each connected
  component is laid out separately and packed.

On a 200k-message corpus (254k nodes, 561k edges), the dashboard builds in about 4 s into a 1.7 MB page.

## Pairing it with open-source visualisers

The dashboard is the overview. These open-source tools (surveyed October 2026) cover the deep dives, and
each one reads an output TGO already writes, without going online:

| Tool | Best for | Feed it |
|---|---|---|
| **WebVOWL** / ProtégéVOWL (MIT; VOWL notation, which the dashboard's schema graph borrows) | browsing the TGO **vocabulary** (TBox) as a diagram | `ontology.ttl` (after conversion to VOWL JSON with OWL2VOWL) |
| **Protégé** (+ OntoGraf) | editing or reviewing the ontology | `ontology.ttl` |
| **Graph Explorer** (Zazuko, a fork of Ontodia) | point-and-click browsing of RDF instances | the Ontop SPARQL endpoint (tier 2, [scaling.md](scaling.md)) or a triple store loaded with `tgo.ttl` / `tgo.nt.gz` |
| **AWS graph-explorer** (Apache-2.0) | the same for property graphs or RDF, run locally | Neo4j (tier 3) or a SPARQL endpoint |
| **Gephi** | publication-quality network figures, layouts and statistics | `graph.gexf` (positions and timeline included) |
| **Neo4j Browser / Bloom** | Cypher exploration with many analysts | `neo4j/` bulk import or `neo4j.cypher` |

Of the libraries assessed for building the dashboard itself:

- **chosen:** Apache ECharts, for chart breadth (graph, Sankey, chord, sunburst and heatmap in one
  Apache-2.0 file);
- **kept:** Cytoscape.js, for the investigation view;
- **optional later:** sigma.js / cosmos.gl, for WebGL rendering above ~10k visible nodes;
- **avoided:** G6, because it spawns web workers that browsers block on `file://`;
- **not reusable:** LodLive (last release 2014) and upstream Ontodia (LGPL plus a commercial edition) are
  dormant or awkward to embed.

## Customising

| Want to change | Where |
|---|---|
| which entity families appear as actors | `ACTOR_FAMILIES` in `tgo/dashboard/build.py` |
| family colours and shapes | `FAMILY_SLOT` / `FAMILY_SHAPE` (slots refer to the palette tokens `--s1…--s8` in `template.html`) |
| relation group colours and names | `EDGE_GROUPS` in `tgo/viz/report.py` (shared with the investigation view) |
| the palette | the `:root` tokens at the top of `tgo/dashboard/template.html`; both themes are defined there |
| a new panel | add the aggregate to `_payload()` / `collect()` / `collect_store()`, then a card plus a `render…()` function in `template.html` |

Rules for changes to the page:

- **Offline:** no external URLs (`offline-check` fails the build), and Apache ECharts stays inlined from
  `tgo/viz/vendor/` with its NOTICE.
- **Safety:** names in the data are attacker-controlled. Render them with `textContent`, or pass them through
  `esc()` before they go into an ECharts tooltip. `tests/test_dashboard.py` checks this.
- **Colour:** use the validated palette in its fixed order. A new categorical colour must pass the palette
  validator in both themes.
