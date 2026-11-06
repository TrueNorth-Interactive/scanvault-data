# ScanVault hosted-asset writers + cadence

Written Nov 6, 2026 (Phase 2D, R33/R38 E-D17–E-D20). One row per hosted
file in this repo. Rule (Nov 4, 2026, owner): **no Muse watcher cron /
GitHub Action is the steady-state writer of any asset.** Where the app
can compute content on the phone it does (card-delta pattern); where it
genuinely cannot, a hosted file is transport and the app fetches it.
Every row names the writer, the cadence, and the human publish step.
"Human publish" = copy the staged file into this working copy, commit,
push — Muse never pushes.

## Sealed catalogue

| file | writer | cadence | publish |
|---|---|---|---|
| `sealed-products.json` (hosted, 271 rows) + bundled copy in `www/data/` (248 rows) | Human curation (manual edits; **surgical line edits only** — whole-file `json.dump` does not round-trip this file) | When a sealed product ships or a UPC is sourced | Human commit + push; bundled copy ships with the next app build |
| `sealed-images/`, `supplies-images/` | Human curation | With the product row | Same commit as the row |

Rules baked into the data (R30, Nov 6, 2026): a barcode shared by two
products is BLANKED on both rows with an `upcNote` naming the twin
(11 pairs blanked hosted, 39 blank total); singly-unsourced UPCs are
blank with a reason (17). `sealedByUpc` returns null on any multi-match,
so a scan never files the wrong variant.

## Set metadata / icons

| file | writer | cadence | publish |
|---|---|---|---|
| `onepiece-set-meta.json` (59 entries, Nov 6, 2026) | `tools/build-opc-set-meta.py` in card-scanner-app (OPTCG API + MANUAL dict for sets the API does not list yet: EB-05, OP-18) | On each new One Piece set release (OP-18 EN Nov 20, 2026; EB-05 Oct 30, 2026) | Human runs the builder, copies the output here + to `www/data/onepiece_set_meta.json` (+ JS mirror `js/opc-meta.js`), commits |
| `ygo-set-icons.json` (`{byName, byCode}`, 5,841 B) | **No generator exists** — hand-curated | When a new Yu-Gi-Oh! set ships: add a `byCode` entry pointing at a representative card image (`images.ygoprodeck.com/...`) | Human edit + commit. Hosted-only (the app fetches it, `sets.js:395`); there is no bundled copy |

## Offline ID layers (Layer 1 maps + Layer 2 hashes)

| file | writer | cadence | publish |
|---|---|---|---|
| `names-yugioh-ids.json` (14,655 rows), `names-mtg-ids.json` (38,700), `names-onepiece-ids.json` (2,867), `names-lorcana-ids.json` (2,694), `names-digimon-ids.json` (5,200), `names-mtg-counts.json` | `phase2b/data/build_maps.py` (v2b.1) from $0 direct sources: YGOPRODeck cardinfo, Scryfall bulk local file, OPTCG API, Lorcana API, DigimonCard.io | On demand — when a new set releases in a genre, or a map is found stale | Human runs the builder, copies to `www/data/` AND here, commits |
| `names-yugioh.json`, `names-mtg.json`, `names-onepiece.json`, `names-lorcana.json`, `names-digimon.json` (full name indexes) | **No generator located in this pass — unowned.** Predates 2B (G4/G5 publishes); refresh via the same pipeline as the `-ids` maps once confirmed | When a new set ships | Human |
| `art-hashes-pokemon.json` (21,988 entries), `art-hashes-yugioh.json`, `art-hashes-onepiece.json`, `art-hashes-digimon.json`, `art-hashes-lorcana.json` | `art-hash-index/build_hashes.py` (dhash over official art) | On demand — new set / newly sourced promo art | Human runs the builder, copies to `www/data/` AND here, commits |
| `art-hashes-pokemon-artbox.json` (21,988 entries) | `art-hash-index/build_artbox_hashes.py` (same dhash over the `VISION_ART_BOX` crop) | With the full Pokémon index | Same commit as the full index |
| `mtg-hashes.json` (119,556 entries, ~7.6 MB) + bundled compact `art-hashes-mtg-compact.json` (38,621) | `art-hash-index/build_hashes.py` from Scryfall bulk `scryfall-default-cards.json` (on disk in `art-hash-index/`) | On demand / manual | Human runs the builder, copies here + bundled compact to `www/data/`, commits |

Residual (R37, Nov 6, 2026): the hosted MTG hash index holds 80,935
non-representative printing uuids that are NOT in the oracle-id map, so
an offline hash hit on one of them misses the map and falls through to
the OCR cascade (graceful, no wrong ID; shortcut lost). Remedy designed,
not built: `mtg-uuid-to-rep.json` (uuid → representative uuid) from the
same Scryfall bulk file, ~6–7 MB, consulted by `canonicalIdForKey`.

## Card database delta — on-phone compute (no hosted writer)

| file | writer | cadence | publish |
|---|---|---|---|
| `card-db-delta.json` (generated 2026-11-02, `cards: []`) | **On-phone compute owns it: `localdb.js refreshLocalDelta`** (fetches new-set details on the device, ADD-ONLY overlay) | N/A — the hosted file is a **dormant fallback**, no longer written by any job since Nov 2, 2026 (weekly cron's hosted sections removed outright, R32) | None. If the fallback is ever needed again, a human publishes a fresh file by hand |

## Market / index data

| file | writer | cadence | publish |
|---|---|---|---|
| `tcg500-index.json`, `tcg500-snapshots.json` ("Pokémon 100") | `tools/build-tcg500-snapshots.py` via the daily cron `scanvault-tcg500-snapshot` (05:11) | Daily | Automatic by the cron (deliberate exception to the no-watcher rule, parent decision 14, Nov 6, 2026 — the 100-file contract, NOT 500; the 49 unpriced Mega Evolution cards WAIT and are excluded from published snapshots, counted as `waitingCount`) |
| `tcg500-constituents.json` (100 entries) | Human curation (ranked list + research notes) | Manual reconstitution only — a research step inside the daily job's own run, never riding another job | Human edit + commit |
| `hot-products.json` | `tools/hot-products-watch.py` via the daily cron `scanvault-hot-products-watch` | Daily | Automatic by the cron — **teardown wired to the phone verdict on v2.528+** (R32/D10); when that verdict lands, the job is removed |
| Sealed draft entries (hosted staging for the drafts screen) | `tools/sealed-draft-entries.py` via the daily cron `scanvault-sealed-drafts` | Daily | Automatic by the cron — **same teardown trigger** (phone verdict on v2.528+) |
| `drops-feed.json` (`_meta.lastReviewed` = 2026-11-02) | `tools/build-drops-feed.py` | Weekly drops research (build-bound since R32 — the weekly cron no longer publishes hosted data) | Human runs the builder, commits |
| `pull-rates.json` (updated 2026-10-30) | **No generator located in this pass — unowned / hand-curated** | When a pull-rate source changes | Human edit + commit |
| `app-config.json` (v2) | App release process | With the release | Ships with the build; hosted copy mirrors it |

## Watchers (not writers)

| file | writer | cadence | publish |
|---|---|---|---|
| `tools/new-set-watch-report.json` / `.md` | `tools/new-set-checklist.md` + the daily cron `scanvault-new-set-watch` (permanent — the other deliberate no-watcher exception) | Daily | Report only — it never writes catalog data. Triage (catalog curation for the flagged sets) is a human/owner decision |

## Vision pack (Layer 3) — BLOCKED

| file | writer | cadence | publish |
|---|---|---|---|
| `vision/` (`dinov2-small-quantized.onnx`, `vision-manifest.json`, `vision-pokemon-whole.f16`, `vision-pokemon-art.f16`, `vision-pokemon-index.json`; Pokémon only, 21,956 refs, built Oct 23, 2026) | **Pipeline NOT LOCATED (Nov 6, 2026).** Exhaustive search of the workspace + git history found the artifacts but no builder script (no DINOv2/ONNX builder anywhere; the pack was built server-side/outside this workspace) | — | Rebuilding is a +~1 week job (DINOv2-small ONNX inference over 21,956 official images × 2 crops, fp16 packing, index generation, bench harness). **Not built in Phase 2D. Layer 3 stays blocked.** |

## Undecided (human call)

- **`showcase.html`** (uncommitted in this working copy, 27+/9- Oct 28 loop
  fixes): push as its own commit, or revert. The weekly cron's
  blanket-add can no longer sweep it (R32 removed `git add -A` from every
  job touching this repo).
- **Weekly cron `scanvault-weekly-card-db-sync`** (Mondays 06:11): re-scoped
  Nov 6, 2026 to build-bound work only (bundled DB refresh +
  drops-feed freshness check + new-set coverage flag as research-only).
  It writes NO hosted files and performs NO git ops in this repo.
