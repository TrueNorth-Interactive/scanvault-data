#!/usr/bin/env python3
"""ScanVault sealed draft entries (dry-run by default, --live publishes).

When a new set releases, its sealed products should appear in the
catalog on their own -- not wait for a human to type entries. This script
drafts catalog entries for new sets (released, <=180 days, no catalog
products yet) from retailer-verified titles ONLY:

- Set detection reuses new-set-watch.py's feed detectors (TCGdex,
  Scryfall, YGOPRODeck, Lorcana API). Digimon has no dated feed and is
  reported, not drafted.
- For each new set, 401 Games suggest is queried with the set name.
  A candidate is drafted ONLY when:
  (1) every significant token of the set name appears in the candidate
      title (same token discipline as the watcher's Tier-1 art gates),
  (2) exactly one sealed-product type maps from the title (see
      map_type), and
  (3) the title carries no language-variant marker (a Japanese/French
      box is a different product, never drafted as the English one).
- Draft fields: id (genre convention + feed code + type slug),
  genre, name (401 title minus the genre prefix, separators
  normalized), set (feed name), setId (feed code, lowercased),
  type, releaseDate (feed date), msrpUSD null, upc "" (barcode
  matching simply never fires for a draft -- the swsh12-etb precedent:
  upc "" already exists in the catalog), NO image key (imageless
  entries omit it; the new-set watcher fills draft images on its next
  run). No prices, no invented products, no guessed UPCs.
- Publish (only with --live): surgical append to sealed-products.json
  (previous last record gains a comma; new records inserted before
  the closing bracket; every old record otherwise byte-identical),
  targeted commit of sealed-products.json ONLY, push, ls-remote check.
  Never touches showcase.html. A 429 from 401 stops the provider for
  the run, never retried around.
"""

import argparse
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(REPO, "sealed-products.json")
GENRES = ["pokemon", "mtg", "lorcana", "onepiece", "yugioh", "digimon"]

_spec = importlib.util.spec_from_file_location(
    "nsw", os.path.join(REPO, "tools", "new-set-watch.py"))
nsw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nsw)

TYPE_SLUGS = {
    "etb": "etb", "booster-box": "booster-box", "bundle": "bundle",
    "trove": "trove", "tin": "tin", "collection": "collection",
    "gift-set": "gift-set", "box": "box",
}
LANGUAGE_MARKERS = ["french", "japanese", "german", "korean", "chinese",
                    "spanish", "portuguese", "italian"]
SINGLE_PAT = re.compile(r"\d{3}/\d{3}|\b(rare|super rare|ultra rare|"
                        r"hyper rare|secret rare)\b.*\d+/\d+")
GENRE_PREFIX = re.compile(
    r"^(Pokemon|Yu-?Gi-?Oh!?|Magic: The Gathering|Disney Lorcana|"
    r"One Piece Card Game|Digimon Card Game)\s*-\s*", re.I)
EDITION_SUFFIX = re.compile(r"\s*-\s*1st Edition\s*$", re.I)


def strip_edition(title):
    return EDITION_SUFFIX.sub("", title or "").strip()


def map_type(title):
    """Exactly one sealed type, or (None, reason). Edition suffixes
    (YGO ' - 1st Edition') are stripped first: they trail the product
    type and would otherwise defeat the endswith(' box') rule."""
    t = strip_edition(title).lower()
    if SINGLE_PAT.search(t):
        return None, "single card, not sealed"
    if any(m in t for m in LANGUAGE_MARKERS):
        return None, "language variant, not drafted as English"
    types = set()
    if "elite trainer box" in t:
        types.add("etb")
    if "booster box" in t or "booster display" in t:
        types.add("booster-box")
    if "bundle" in t:
        types.add("bundle")
    if "trove" in t:
        types.add("trove")
    if re.search(r"\btin\b", t):
        types.add("tin")
    if "collection" in t or "starter set" in t:
        types.add("collection")
    if "gift set" in t:
        types.add("gift-set")
    if not types and t.rstrip().endswith(" box"):
        types.add("box")
    if len(types) == 1:
        return types.pop(), ""
    if not types:
        return None, "no mappable sealed type"
    return None, f"ambiguous types: {sorted(types)}"


def clean_draft_name(title):
    n = GENRE_PREFIX.sub("", strip_edition(title)).strip()
    n = n.replace(" - ", " \u2014 ")
    n = n.replace("Scarlet and Violet", "Scarlet & Violet")
    n = n.replace("Pokemon Center", "Pok\u00e9mon Center")
    return re.sub(r"\s+", " ", n).strip()


def suggest_401_draft(query):
    """401 suggest with a wider limit: drafts want every sealed product
    of the set, not just the top 5 the art matcher needs."""
    import urllib.parse
    q = urllib.parse.urlencode(
        {"q": query, "resources[type]": "product",
         "resources[limit]": 25})
    url = f"https://store.401games.ca/search/suggest.json?{q}"
    _, _, data = nsw.http_get(url, urllib_fallback=False)
    d = json.loads(data.decode("utf-8", "replace"))
    return d.get("resources", {}).get("results", {}).get("products", [])


def draft_id(genre, feed_id, set_name, type_slug, exclusive):
    code = re.sub(r"[^a-z0-9]", "", (feed_id or "").lower())
    if not code:
        code = re.sub(r"[^a-z0-9]+", "-",
                      set_name.lower()).strip("-")[:24]
    prefix = {"pokemon": "", "mtg": "mtg-", "lorcana": "",
              "onepiece": "op-", "yugioh": "yg-",
              "digimon": "digi-"}.get(genre, "")
    base = f"{prefix}{code}-{type_slug}"
    if exclusive:
        base += "-pc"
    return base


def git(*args):
    return subprocess.check_output(["git"] + list(args), cwd=REPO,
                                   text=True,
                                   stderr=subprocess.STDOUT).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    from zoneinfo import ZoneInfo
    today = datetime.datetime.now(
        ZoneInfo("America/Toronto")).date()
    today_s = today.isoformat()

    with open(CATALOG) as f:
        raw = f.read()
    catalog = json.loads(raw)
    by_genre_sets = {}
    for p in catalog:
        s = by_genre_sets.setdefault(p["genre"], set())
        s.add(nsw.norm_name(p.get("set")))
        if p.get("setId"):
            s.add(p["setId"])
    existing_ids = {p["id"] for p in catalog}

    detectors = [("pokemon", nsw.detect_pokemon), ("mtg", nsw.detect_mtg),
                 ("yugioh", nsw.detect_yugioh),
                 ("lorcana", nsw.detect_lorcana),
                 ("onepiece", nsw.detect_onepiece)]
    new_sets = []
    notes = []
    for genre, fn in detectors:
        try:
            found, n = fn(by_genre_sets.get(genre, set()))
        except Exception as e:
            notes.append(f"{genre} detector crashed: {e}")
            continue
        notes.extend(n)
        for s in found:
            rd = (s.get("releaseDate") or "")[:10]
            if not rd or rd > today_s:
                continue
            age = (today - datetime.date.fromisoformat(rd)).days
            if 0 <= age <= 180:
                new_sets.append(s)
    notes.append("digimon: no dated sets feed; new Digimon sets are "
                 "not drafted by this run")

    drafts, skipped = [], []
    stop = False
    for s in new_sets:
        if stop:
            skipped.append((s, "provider stopped (429)"))
            continue
        time.sleep(3)
        try:
            cands = suggest_401_draft(s["name"])
        except Exception as e:
            if "429" in str(e):
                stop = True
                skipped.append((s, "provider stopped (429)"))
            else:
                skipped.append((s, f"suggest failed: {e}"))
            continue
        set_toks = nsw.tokens(s["name"]) - nsw.GENERIC_TOKENS
        drafted_here = False
        for c in cands:
            title = c.get("title") or ""
            if not set_toks <= nsw.tokens(title):
                continue
            ttype, reason = map_type(title)
            if not ttype:
                skipped.append((s, f"{title[:60]}: {reason}"))
                continue
            is_pc = (ttype == "etb" and
                     ("pokemon center" in title.lower() or
                      "pok\u00e9mon center" in title.lower()))
            exclusive = "pokemon-center" if is_pc else None
            did = draft_id(s["genre"], s.get("feed_id"), s["name"],
                           ttype, exclusive)
            if did in existing_ids:
                skipped.append((s, f"{title[:60]}: id {did} exists"))
                continue
            rec = {"genre": s["genre"], "id": did, "msrpUSD": None,
                   "name": clean_draft_name(title),
                   "releaseDate": (s.get("releaseDate") or "")[:10],
                   "set": s["name"],
                   "setId": (s.get("feed_id") or "").lower(),
                   "type": ttype, "upc": ""}
            if exclusive:
                rec["exclusive"] = exclusive
            drafts.append(rec)
            existing_ids.add(did)
            drafted_here = True
        if not drafted_here:
            skipped.append((s, "no draftable candidate"))

    print(f"Sealed draft entries "
          f"({'LIVE' if args.live else 'DRY-RUN'}) {today_s}")
    print(f"New sets checked: {len(new_sets)}")
    for r in drafts:
        print(f"  DRAFT {r['id']} | {r['name']}")
    for s, reason in skipped:
        print(f"  SKIP {s['genre']} {s['name']}: {reason}")
    for n in notes:
        print(f"  NOTE: {n}")
    if not args.live:
        print("\nDRY-RUN: nothing written or published.")
        return 0
    if not drafts:
        print("\nNo drafts; nothing to publish.")
        return 0

    # Surgical append: previous last record gains a comma; new records
    # go before the closing bracket, in the file's own record shape.
    lines = raw.split("\n")
    close_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == "]":
            close_idx = i
            break
    assert close_idx is not None
    last_rec = close_idx - 1
    assert lines[last_rec].strip() == "}", lines[last_rec]
    lines[last_rec] = lines[last_rec] + ","
    order = ["genre", "id", "msrpUSD", "name", "releaseDate", "set",
             "setId", "type", "exclusive", "upc"]
    block = []
    for j, r in enumerate(drafts):
        block.append("  {")
        keys = [k for k in order if k in r]
        for k_i, k in enumerate(keys):
            comma = "," if k_i < len(keys) - 1 else ""
            block.append(f"    {json.dumps(k)}: "
                         f"{json.dumps(r[k], ensure_ascii=False)}{comma}")
        block.append("  }" + ("," if j < len(drafts) - 1 else ""))
    lines[close_idx:close_idx] = block
    new_raw = "\n".join(lines)
    new = json.loads(new_raw)
    assert len(new) == len(catalog) + len(drafts)
    assert [p["id"] for p in new[:len(catalog)]] == \
        [p["id"] for p in catalog]
    for o, n in zip(catalog, new):
        assert o == n, f"old record changed: {o['id']}"
    for r in drafts:
        assert r["id"] and r["name"] and r["type"] in TYPE_SLUGS
        assert r["genre"] in GENRES and "image" not in r
    with open(CATALOG, "w") as f:
        f.write(new_raw)
    git("add", "sealed-products.json")
    staged = git("diff", "--cached", "--name-only").split()
    assert staged == ["sealed-products.json"], staged
    sets = sorted({r["set"] for r in drafts})
    git("commit", "-m", "Sealed catalog (watch): draft "
        f"{len(drafts)} products for {', '.join(sets)}")
    git("push", "origin", "main")
    head = git("rev-parse", "HEAD")
    remote = subprocess.check_output(
        ["git", "ls-remote", "origin", "main"], text=True).split()[0]
    assert remote == head, f"push not landed: {remote} != {head}"
    print(f"\nPUBLISHED: {head}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
