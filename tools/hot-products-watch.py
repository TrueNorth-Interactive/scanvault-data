#!/usr/bin/env python3
"""ScanVault hot-products watcher (DRY-RUN first, --live publishes).

Automatic Hot Products lineup for the Home tab: 4 products per TCG genre
(pokemon, mtg, lorcana, onepiece, yugioh, digimon), refreshed daily.

HONEST SIGNAL NOTE (Nov 4, 2026 research): no free source publishes a
sealed-product sales ranking that this machine can read. Verified dead:
401 Games Best Sellers (board games only, 1500 products, 0 TCG), 401
genre collections (singles-dominated, no sales order), eBay sold search
(403), Reddit hot.json (empty), PriceCharting (direct HTTP 403 from this
VM by standing rule), TCGplayer (no server-rendered ranking),
PokemonPriceTracker (user-ruled evaluation-only, no data in the app).
So this picker does NOT claim a sales rank. Its rule is: newest RELEASED
sealed products with verified art, per genre. New sets rotate in as they
release and their art lands; older ones age out. That is the rotation the
user approved as the honest automatic fallback.

Rules:
- releaseDate must be <= today (future-dated pre-release entries are
  excluded -- you cannot buy them yet).
- Art-gated: only products with a verified catalog image (raw
  scanvault-data URL, which the app's isTrustedImageUrl accepts) are
  picked. Newer products without art are reported, not shipped imageless
  -- the new-set watcher fills their art, then they rotate in.
- name: catalog name minus the leading brand prefix
  ("Magic: The Gathering -- ", "Disney Lorcana: ", "One Piece Card Game
  -- ", "Yu-Gi-Oh! TCG: ", "Pokemon TCG: ").
- q: genre search phrase + cleaned name (the tile links to an eBay
  search for q, same as the manual file).
- tag: "Hot" for every item.
- Output shape mirrors the hotProducts validator: per-genre list <= 8,
  name <= 200 chars, q <= 200 chars, image trusted or absent.
- Publish (only with --live): targeted commit of hot-products.json
  ONLY, push, ls-remote check. Never touches showcase.html, never adds
  prices, never changes any field except the lineup itself.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(REPO, "sealed-products.json")
OUT = os.path.join(REPO, "hot-products.json")
GENRES = ["pokemon", "mtg", "lorcana", "onepiece", "yugioh", "digimon"]
GENRE_PHRASE = {
    "pokemon": "pokemon",
    "mtg": "magic the gathering",
    "lorcana": "disney lorcana",
    "onepiece": "one piece card game",
    "yugioh": "yugioh",
    "digimon": "digimon card game",
}
BRAND_PREFIX = re.compile(
    r"^(Magic: The Gathering\s*(--|—|–|:)\s*|Disney Lorcana:\s*|"
    r"One Piece Card Game\s*(--|—|–|:)\s*|Yu-Gi-Oh! TCG:\s*|"
    r"Pok[eé]mon TCG:\s*)",
    re.I,
)


def clean_name(name):
    n = BRAND_PREFIX.sub("", name or "").strip()
    n = re.sub(r"\[[^\]]*\]", " ", n)  # drop [OP-18]-style set codes
    return re.sub(r"\s+", " ", n).strip()


def make_q(genre, name):
    import unicodedata
    q = GENRE_PHRASE[genre] + " " + clean_name(name)
    q = q.lower()
    q = unicodedata.normalize("NFKD", q).encode("ascii", "ignore").decode()
    q = re.sub(r"[&]", " and ", q)
    q = re.sub(r"[^a-z0-9 ]+", " ", q)
    return re.sub(r"\s+", " ", q).strip()[:200]


def git(*args):
    return subprocess.check_output(
        ["git", "-C", REPO] + list(args), text=True).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="publish the new lineup (default: dry-run)")
    args = ap.parse_args()
    # Toronto date, ALWAYS: date.today() follows the ambient timezone,
    # which differs between execution contexts (cron worker = Toronto,
    # exec shell = UTC). In the evening hours that flips the `updated`
    # stamp by a day and breaks the no-change comparison. The app's
    # audience is Toronto, so the watcher's day is Toronto's day.
    from zoneinfo import ZoneInfo
    today = datetime.datetime.now(
        ZoneInfo("America/Toronto")).date().isoformat()

    with open(CATALOG) as f:
        prods = json.load(f)
    old = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            old = json.load(f)

    lineup = {}
    report = {"newer_no_art": {}, "future_excluded": {}, "held": {}}
    for g in GENRES:
        items = [p for p in prods if p.get("genre") == g]
        released = [p for p in items
                    if (p.get("releaseDate") or "") <= today]
        future = [p for p in items
                  if (p.get("releaseDate") or "") > today]
        report["future_excluded"][g] = len(future)
        released.sort(key=lambda p: p.get("releaseDate") or "",
                      reverse=True)
        with_art = [p for p in released if p.get("image")]
        no_art = [p for p in released if not p.get("image")]
        report["newer_no_art"][g] = [
            p["name"] for p in no_art[:3]]
        picks = with_art[:4]
        if len(picks) < 4 and old.get(g):
            # Hold previous verified items for unfilled slots; never
            # ship a short or imageless lineup silently.
            have = {clean_name(p["name"]) for p in picks}
            for prev in old[g]:
                if len(picks) >= 4:
                    break
                if clean_name(prev["name"]) not in have:
                    picks.append(prev)
                    report["held"].setdefault(g, []).append(prev["name"])
        lineup[g] = [{
            "image": p["image"],
            "name": clean_name(p["name"]),
            "q": make_q(g, p["name"]),
            "tag": "Hot",
        } for p in picks]

    out = dict(lineup)
    out["note"] = ("Home Hot Products, per genre. Refreshed daily by the "
                   "hot-products watcher: newest released sealed products "
                   "with verified art, per genre. Not a sales ranking -- "
                   "no free source publishes one this machine can read.")
    out["updated"] = today

    print(f"Hot-products dry-run ({today})"
          if not args.live else f"Hot-products LIVE run ({today})")
    for g in GENRES:
        print(f"--- {g} ---")
        for p in lineup[g]:
            print(f"  {p['name']}  [q: {p['q']}]")
        if report["newer_no_art"][g]:
            print(f"  (newer, no art yet: "
                  f"{'; '.join(report['newer_no_art'][g])})")
        if report["held"].get(g):
            print(f"  (held from previous lineup: "
                  f"{'; '.join(report['held'][g])})")
        print(f"  (future-dated excluded: "
              f"{report['future_excluded'][g]})")

    if not args.live:
        print("\nDRY-RUN: nothing written or published.")
        return 0

    # Shape check mirroring the app's hotProducts validator.
    for g in GENRES:
        assert len(lineup[g]) <= 8
        for p in lineup[g]:
            assert p["name"] and len(p["name"]) <= 200
            assert p["q"] and len(p["q"]) <= 200
            assert p["image"].startswith(
                "https://raw.githubusercontent.com/"
                "TrueNorth-Interactive/scanvault-data/main/"), p
    # No-op path (Nov 4 fix): on a no-change day the lineup is identical
    # to the committed file, so git add stages nothing and the old
    # staged-assertion failed the run. Compare the LINEUP (not the
    # `updated` stamp, which is informational) and exit cleanly -- a
    # no-change day is a success, not a failure, and must not produce
    # a failure report or a date-only commit.
    new_text = json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    if os.path.exists(OUT):
        try:
            with open(OUT) as f:
                existing = json.load(f)
            existing_lineup = {g: existing.get(g) for g in GENRES}
            if existing_lineup == lineup:
                print("\nNO CHANGE: lineup identical to the published "
                      "file; nothing to commit.")
                return 0
        except (json.JSONDecodeError, OSError):
            pass
    with open(OUT, "w") as f:
        f.write(new_text)
    git("add", "hot-products.json")
    staged = git("diff", "--cached", "--name-only").split()
    assert staged == ["hot-products.json"], staged
    git("commit", "-m", "Hot products (watch): daily auto lineup")
    git("push", "origin", "main")
    head = git("rev-parse", "HEAD")
    remote = subprocess.check_output(
        ["git", "ls-remote", "origin", "main"], text=True).split()[0]
    assert remote == head, f"push not landed: {remote} != {head}"
    print(f"\nPUBLISHED: {head}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
