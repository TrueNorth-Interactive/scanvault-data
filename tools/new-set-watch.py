#!/usr/bin/env python3
"""New-set + missing-image watcher for ScanVault sealed products.

Daily job (user order Nov 4, 2026): detect new sets across TCG feeds, and
source verified box art for catalog products that lack images, publishing
to the hosted data so phones pick it up on their next daily check -- no
APK update, no manual run from the user.

TIER-1 (auto-publish, strict): a candidate publishes ONLY when ALL hold:
  * source is the allowlisted retailer suggest API (401 Games) and the
    image is that retailer's featured product image (never a search
    screenshot, contents spread source, or hotlink -- the file is
    mirrored into sealed-images/ and the catalog points at the raw URL);
  * token agreement: every significant token of the catalog product name
    appears in the candidate title (set + type + variant distinguisher);
  * variant agreement: variant tokens (center, gift, and language tokens)
    agree exactly between catalog name and candidate title -- a regular
    ETB never takes a Pokemon Center shot, and vice versa;
  * image downloads, is HTTP 200 + image/*, valid magic bytes, >= 1 KB,
    and >= 200 px on each side (PIL).
Anything failing a check goes to TIER-2 (report only, never published).

Gates inherited from the box-art sweep (Nov 4, 2026):
  * NEVER touch any catalog field except `image`; never reorder records.
  * Targeted `git add` of sealed-products.json + the new image files
    ONLY -- showcase.html (pre-existing modification) is never swept in.
  * After wiring, re-validate: JSON parses, record count/order unchanged,
    only image fields differ, every image URL matches the raw pattern.
  * Live-verify each new raw URL (200 + image/*) after push, and confirm
    remote HEAD via `git ls-remote` before reporting success.
  * On HTTP 429 from a provider: STOP that provider's calls for the run
    (no retry loop, no endpoint switching to dodge the limit).

Detection feeds (the same feeds the app itself uses):
  * pokemon: api.tcgdex.net/v2/en/sets (+ per-set detail for unmatched
    only, since the list carries no release dates).
  * mtg: api.scryfall.com/sets (paginated; released_at; expansion/core).
  * yugioh: db.ygoprodeck.com/api/v7/cardsets.php (tcg_date).
  * lorcana: api.lorcana-api.com/sets/all (Release_Date).
  * onepiece: optcgapi allSets -- DEGRADED: unreachable from this VM
    (connection failure), so One Piece new sets are NOT detected here.
  * digimon: DEGRADED: the app derives Digimon sets from a bulk card
    download with no release dates, so no dated feed exists to watch.

Usage: new-set-watch.py [--dry-run] [--max-fill N] [--report-dir DIR]
Dry-run performs detection + candidate checks (downloading candidates to
/tmp for verification) but writes nothing to the repo and pushes nothing.
"""
import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(REPO, "sealed-products.json")
IMG_DIR = os.path.join(REPO, "sealed-images")
RAW_BASE = ("https://raw.githubusercontent.com/TrueNorth-Interactive/"
            "scanvault-data/main/sealed-images/")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ScanVaultWatch/1.0",
      "Accept": "application/json, image/*, */*"}
TODAY = time.strftime("%Y-%m-%d")

# Transport note (2026-11-04): curl is the primary transport because
# Python's TLS is filtered/reset by this machine's egress proxy for
# several hosts (verified: curl returned 200 where urllib got
# IncompleteRead or Scryfall 400, even with ALPN set). urllib is kept
# as a single fallback attempt for feed/image fetches only -- never for
# the 401 suggest endpoint, where urllib fails deterministically.
import tempfile
_CTX = ssl.create_default_context()
_CTX.set_alpn_protocols(["http/1.1"])
_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=_CTX))


def _curl_fetch(url, timeout=30):
    with tempfile.TemporaryDirectory() as td:
        body_p, hdr_p = os.path.join(td, "b"), os.path.join(td, "h")
        r = subprocess.run(
            ["curl", "-s", "-g", "--max-time", str(timeout),
             "-A", UA["User-Agent"], "-D", hdr_p, "-o", body_p, url],
            capture_output=True, timeout=timeout + 10)
        code, ctype, data = None, "", b""
        if os.path.exists(hdr_p):
            for line in open(hdr_p, "rb").read().decode(
                    "iso-8859-1", "replace").splitlines():
                if line.startswith("HTTP/"):
                    try:
                        code = int(line.split()[1])
                    except (IndexError, ValueError):
                        pass
                elif line.lower().startswith("content-type:"):
                    ctype = line.split(":", 1)[1].strip()
        if os.path.exists(body_p):
            data = open(body_p, "rb").read()
        if code is None:
            raise RuntimeError(
                f"curl failed rc={r.returncode}: "
                f"{r.stderr[:200]!r}")
        return code, ctype, data


def _urllib_fetch(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with _OPENER.open(req, timeout=timeout) as r:
        return r.status, r.headers.get_content_type(), r.read()


def http_get(url, timeout=30, urllib_fallback=True):
    """GET returning (status, content_type, bytes). Raises HTTPError for
    4xx/5xx (429 included, so callers can stop on rate limits)."""
    code, ctype, data = _curl_fetch(url, timeout)
    if code == 429:
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
    if code >= 400:
        if urllib_fallback:
            try:
                return _urllib_fetch(url, timeout)
            except Exception:
                pass
        raise urllib.error.HTTPError(url, code, f"HTTP {code}", {}, None)
    return code, ctype, data


def open_retry(req, timeout=30):
    """Kept for compatibility; http_get is the transport used now."""
    return _OPENER.open(req, timeout=timeout)

GENERIC_TOKENS = {"pokemon", "magic", "gathering", "disney", "lorcana",
                  "one", "piece", "card", "game", "tcg", "trading"}
VARIANT_TOKENS = {"center", "gift", "french", "japanese", "german",
                  "korean", "chinese", "spanish", "portuguese", "italian"}
TYPE_PHRASES = [
    ("gift bundle", "gift bundle"),
    ("elite trainer box", "elite trainer box"),
    ("booster display", "booster box"),
    ("booster box", "booster box"),
    ("illumineers trove", "trove"),
    ("trove", "trove"),
    ("starter deck", "starter deck"),
    ("tamer goods set", "tamer goods set"),
    ("gift set", "gift set"),
    ("bundle", "bundle"),
    ("collection", "collection"),
    ("tin", "tin"),
    ("box", "box"),
    ("etb", "elite trainer box"),
]


def tokens(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def required_tokens(product):
    """Required tokens for a Tier-1 title match, built from the SET name
    + the type phrase + parenthetical distinguishers + variant markers --
    NOT from the full catalog name: the series prefix ("Sword & Shield",
    "Scarlet & Violet") is absent from retailer titles, so requiring it
    falsely rejected exact matches (Lost Origin ETB, Silver Tempest ETB
    in the Nov 4 dry run)."""
    name = product.get("name") or ""
    toks = tokens(product.get("set"))
    toks |= tokens(type_phrase(name, product.get("type")))
    for group in re.findall(r"\(([^)]*)\)", name):
        toks |= tokens(group)
    toks |= variant_set(name, product.get("exclusive"))
    return toks - GENERIC_TOKENS


def variant_set(name, exclusive):
    toks = tokens(name) | tokens((exclusive or "").replace("-", " "))
    return {t for t in toks if t in VARIANT_TOKENS}


def type_phrase(name, ptype):
    nl = (name or "").lower()
    for needle, phrase in TYPE_PHRASES:
        if needle in nl:
            return phrase
    return (ptype or "").lower()


def fetch_json(url, timeout=30):
    _, _, data = http_get(url, timeout)
    return json.loads(data.decode("utf-8", "replace"))


def fetch_bytes(url, timeout=30):
    return http_get(url, timeout)


def norm_name(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# ---------------- detection ----------------

def detect_pokemon(catalog_sets):
    out, notes = [], []
    try:
        feed = fetch_json("https://api.tcgdex.net/v2/en/sets")
    except Exception as e:
        return out, [f"pokemon feed failed: {e}"]
    unmatched = []
    for s in feed:
        if norm_name(s.get("name")) in catalog_sets or \
                (s.get("id") or "") in catalog_sets:
            continue
        unmatched.append(s)
    for s in unmatched[:30]:
        try:
            d = fetch_json(f"https://api.tcgdex.net/v2/en/sets/{s['id']}")
            rd = (d.get("releaseDate") or "")
        except Exception:
            rd = ""
        out.append({"genre": "pokemon", "feed_id": s.get("id"),
                    "name": s.get("name"), "releaseDate": rd})
    if len(unmatched) > 30:
        notes.append(f"pokemon: {len(unmatched) - 30} unmatched sets "
                     "left date-unverified (detail cap)")
    return out, notes


def detect_mtg(catalog_sets):
    out, notes = [], []
    try:
        page = fetch_json("https://api.scryfall.com/sets")
        sets = list(page.get("data", []))
        pages = 1
        while page.get("has_more") and page.get("next_page") and pages < 12:
            time.sleep(0.2)
            page = fetch_json(page["next_page"])
            sets.extend(page.get("data", []))
            pages += 1
    except Exception as e:
        return out, [f"mtg feed failed: {e}"]
    for s in sets:
        if s.get("set_type") not in ("expansion", "core"):
            continue
        if norm_name(s.get("name")) in catalog_sets or \
                (s.get("code") or "") in catalog_sets:
            continue
        out.append({"genre": "mtg", "feed_id": s.get("code"),
                    "name": s.get("name"),
                    "releaseDate": s.get("released_at") or ""})
    return out, notes


def detect_yugioh(catalog_sets):
    out, notes = [], []
    try:
        feed = fetch_json("https://db.ygoprodeck.com/api/v7/cardsets.php")
    except Exception as e:
        return out, [f"yugioh feed failed: {e}"]
    for s in feed:
        if norm_name(s.get("set_name")) in catalog_sets or \
                (s.get("set_code") or "") in catalog_sets:
            continue
        out.append({"genre": "yugioh", "feed_id": s.get("set_code"),
                    "name": s.get("set_name"),
                    "releaseDate": s.get("tcg_date") or ""})
    return out, notes


def detect_lorcana(catalog_sets):
    out, notes = [], []
    try:
        feed = fetch_json("https://api.lorcana-api.com/sets/all")
    except Exception as e:
        return out, [f"lorcana feed failed: {e}"]
    for s in feed:
        if norm_name(s.get("Name")) in catalog_sets or \
                (s.get("Set_ID") or "") in catalog_sets:
            continue
        out.append({"genre": "lorcana", "feed_id": s.get("Set_ID"),
                    "name": s.get("Name"),
                    "releaseDate": s.get("Release_Date") or ""})
    return out, notes


def detect_onepiece(catalog_sets):
    try:
        feed = fetch_json("https://www.optcgapi.com/api/allSets/")
        out = []
        for s in feed if isinstance(feed, list) else []:
            nm = s.get("Name") or s.get("name") or ""
            if norm_name(nm) in catalog_sets:
                continue
            out.append({"genre": "onepiece", "feed_id": s.get("Set_ID"),
                        "name": nm,
                        "releaseDate": s.get("Release_Date") or ""})
        return out, []
    except Exception as e:
        return [], [f"onepiece detection DEGRADED: optcgapi unreachable "
                    f"from this machine ({e}); new One Piece sets are "
                    "not detected by this run"]


# ---------------- Tier-1 candidate sourcing (401 Games suggest) ----------------

def suggest_401(query):
    q = urllib.parse.urlencode(
        {"q": query, "resources[type]": "product", "resources[limit]": 5})
    url = f"https://store.401games.ca/search/suggest.json?{q}"
    _, _, data = http_get(url, urllib_fallback=False)
    d = json.loads(data.decode("utf-8", "replace"))
    return d.get("resources", {}).get("results", {}).get("products", [])


def check_image(url, dest_tmp):
    try:
        status, ctype, data = fetch_bytes(url)
    except Exception as e:
        return False, f"image fetch failed: {e}"
    if status != 200 or not ctype.startswith("image/"):
        return False, f"image HTTP {status} {ctype}"
    if len(data) < 1024:
        return False, f"image too small ({len(data)}B)"
    magic_ok = (data[:3] == b"\xff\xd8\xff" or
                data[:8] == b"\x89PNG\r\n\x1a\n" or
                (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))
    if not magic_ok:
        return False, "image bad magic bytes"
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(data))
        if im.width < 200 or im.height < 200:
            return False, f"image too small px ({im.width}x{im.height})"
    except ImportError:
        pass
    except Exception as e:
        return False, f"image undecodable: {e}"
    with open(dest_tmp, "wb") as f:
        f.write(data)
    ext = { "image/jpeg": "jpg", "image/png": "png",
            "image/webp": "webp" }.get(ctype)
    if not ext:
        m = re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", url.lower())
        ext = "jpg" if not m else ("jpg" if m.group(1) == "jpeg"
                                    else m.group(1))
    return True, ext


def candidate_for(product):
    """Return (ok, info): Tier-1 candidate or the reason it failed."""
    phrase = type_phrase(product.get("name"), product.get("type"))
    q = f"{product.get('set')} {phrase}"
    if (product.get("exclusive") or "") == "pokemon-center" or \
            "center" in tokens(product.get("name")):
        q += " pokemon center"
    try:
        results = suggest_401(q)
    except Exception as e:
        if "429" in str(e):
            return False, {"reason": "provider 429 -- stop", "stop": True}
        return False, {"reason": f"suggest failed: {e}"}
    if not results:
        return False, {"reason": "no suggest candidates"}
    want = required_tokens(product)
    want_var = variant_set(product.get("name"), product.get("exclusive"))
    tried = []
    for cand in results:
        title = cand.get("title") or ""
        ctoks = tokens(title)
        missing = want - ctoks
        if missing:
            tried.append(f"title missing {sorted(missing)[:4]}: {title[:70]}")
            continue
        if variant_set(title, "") != want_var:
            tried.append(f"variant mismatch: {title[:70]}")
            continue
        img = cand.get("image") or ""
        if not img or not img.startswith("http"):
            tried.append(f"no image url: {title[:70]}")
            continue
        tmp = f"/tmp/watch-{product['id']}.bin"
        ok, info = check_image(img, tmp)
        if not ok:
            tried.append(f"image check: {info}")
            continue
        return True, {"title": title, "img_url": img, "tmp": tmp,
                      "ext": info}
    return False, {"reason": "no candidate passed: " + " | ".join(tried[:3])}


# ---------------- repo ops ----------------

def git(*args):
    return subprocess.check_output(["git"] + list(args), cwd=REPO,
                                   text=True, stderr=subprocess.STDOUT)


def validate_wiring(old, new):
    if len(old) != len(new):
        return "record count changed"
    if [p["id"] for p in old] != [p["id"] for p in new]:
        return "record order changed"
    for o, n in zip(old, new):
        for k in n:
            if k != "image" and n[k] != o.get(k):
                return f"non-image change: {n['id']}.{k}"
        for k in o:
            if k not in n:
                return f"dropped field: {n['id']}.{k}"
        if n.get("image") and not n["image"].startswith(RAW_BASE):
            return f"bad image url: {n['id']}"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-fill", type=int, default=25)
    ap.add_argument("--report-dir", default=os.path.dirname(
        os.path.abspath(__file__)))
    args = ap.parse_args()

    with open(CATALOG) as f:
        catalog = json.load(f)
    by_genre_sets = {}
    for p in catalog:
        s = by_genre_sets.setdefault(p["genre"], set())
        s.add(norm_name(p.get("set")))
        if p.get("setId"):
            s.add(p["setId"])
    missing = [p for p in catalog if not p.get("image")]

    report = {"date": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
              "dry_run": args.dry_run, "new_sets": [], "gaps": [],
              "notes": [], "tier1": [], "tier2": [], "degraded": []}
    detectors = [("pokemon", detect_pokemon), ("mtg", detect_mtg),
                 ("yugioh", detect_yugioh), ("lorcana", detect_lorcana),
                 ("onepiece", detect_onepiece)]
    for genre, fn in detectors:
        try:
            found, notes = fn(by_genre_sets.get(genre, set()))
        except Exception as e:
            report["notes"].append(f"{genre} detector crashed: {e}")
            continue
        report["notes"].extend(notes)
        for s in found:
            rd = (s.get("releaseDate") or "")[:10]
            if rd and rd <= TODAY:
                # "New" = released in the last 180 days; unmatched sets
                # 181 days to 5 years old are a bounded backfill triage
                # bucket; older unmatched sets (promos, 1993-2020 back
                # catalog) are counted only, never listed -- otherwise
                # the daily report drowns in 1,000+ ancient sets.
                import datetime
                age = (datetime.date.fromisoformat(TODAY) -
                       datetime.date.fromisoformat(rd)).days
                s["status"] = "released"
                if age <= 180:
                    report["new_sets"].append(s)
                elif age <= 5 * 365:
                    report["gaps"].append(s)
                else:
                    report["old_omitted"] = report.get("old_omitted", 0) + 1
            elif rd:
                s["status"] = "upcoming"
            else:
                s["status"] = "date-unknown"
                report["gaps"].append(s)
    report["degraded"].append(
        "digimon: no dated sets feed (app derives sets from a bulk card "
        "download); new Digimon sets are not detected by this run")

    # Tier-1 auto-fill for missing-image entries.
    fills = []
    stop_provider = False
    for p in missing[:args.max_fill]:
        if stop_provider:
            report["tier2"].append({"id": p["id"], "genre": p["genre"],
                                    "name": p.get("name"),
                                    "reason": "provider stopped (429)"})
            continue
        time.sleep(3)
        ok, info = candidate_for(p)
        if not ok:
            if info.get("stop"):
                stop_provider = True
            report["tier2"].append({"id": p["id"], "genre": p["genre"],
                                    "name": p.get("name"),
                                    "reason": info["reason"]})
            continue
        fills.append((p, info))
        report["tier1"].append({"id": p["id"], "genre": p["genre"],
                                "name": p.get("name"),
                                "candidate": info["title"]})
    if len(missing) > args.max_fill:
        report["notes"].append(
            f"{len(missing) - args.max_fill} missing-image entries left "
            "for later runs (max-fill cap)")

    if fills and not args.dry_run:
        # Surgical line edits (NOT a whole-file json.dump): the catalog
        # file's exact formatting is preserved byte-for-byte except the
        # added image lines, matching the sweep's minimal-diff shape.
        with open(CATALOG) as f:
            lines = f.read().split("\n")
        for p, info in fills:
            ext = info["ext"]
            url = RAW_BASE + f"{p['id']}.{ext}"
            id_line = None
            for i, ln in enumerate(lines):
                if ln.strip() == f'"id": "{p["id"]}",':
                    id_line = i
                    break
            assert id_line is not None, f"id line not found: {p['id']}"
            j = id_line
            while lines[j].strip() not in ("},", "}"):
                j += 1
            last = lines[j - 1]
            assert not last.rstrip().endswith(","), \
                f"unexpected trailing comma before close: {p['id']}"
            lines[j - 1] = last + ","
            lines.insert(j, f'    "image": "{url}"')
        new_text = "\n".join(lines)
        try:
            new_catalog = json.loads(new_text)
        except Exception as e:
            print(f"REPARSE FAILED after surgical edit: {e} -- aborting")
            sys.exit(2)
        err = validate_wiring(catalog, new_catalog)
        if err:
            print(f"VALIDATION FAILED: {err} -- aborting, no commit")
            sys.exit(2)
        added_files = []
        for p, info in fills:
            ext = info["ext"]
            dest = os.path.join(IMG_DIR, f"{p['id']}.{ext}")
            with open(info["tmp"], "rb") as f:
                data = f.read()
            with open(dest, "wb") as f:
                f.write(data)
            added_files.append(dest)
        with open(CATALOG, "w") as f:
            f.write(new_text)
        try:
            git("add", "sealed-products.json",
                *[os.path.relpath(d, REPO) for d in added_files])
            staged = git("diff", "--cached", "--name-only").split()
            assert all(s == "sealed-products.json" or
                       s.startswith("sealed-images/") for s in staged), \
                f"unexpected staged files: {staged}"
            git("commit", "-m",
                f"Sealed box art (watch): {len(fills)} products")
            git("push", "origin", "main")
            head = git("rev-parse", "HEAD").strip()
            remote = subprocess.check_output(
                ["git", "ls-remote", "origin", "main"], cwd=REPO,
                text=True).split()[0]
            assert remote == head, f"remote {remote} != local {head}"
            report["commit"] = head
            # Live-verify each new raw URL.
            for p, info in fills:
                ext = info["ext"]
                url = RAW_BASE + f"{p['id']}.{ext}"
                try:
                    status, ctype, _ = fetch_bytes(url)
                    assert status == 200 and ctype.startswith("image/")
                except Exception as e:
                    report["notes"].append(
                        f"LIVE-VERIFY FAILED for {p['id']}: {e}")
        except Exception as e:
            report["notes"].append(f"PUBLISH FAILED: {e}")
            print(f"PUBLISH FAILED: {e}")
            sys.exit(3)

    md = [f"# New-set watch report ({report['date']})",
          f"Dry run: {args.dry_run}",
          "", "## New released sets with no catalog products (<=180 days)",
          ("(none -- every recently released set already has catalog "
           "products)" if not report["new_sets"] else "")]
    for s in report["new_sets"]:
        md.append(f"- {s['genre']}: {s['name']} "
                  f"(feed id {s.get('feed_id')}, released "
                  f"{s.get('releaseDate') or '?'}) -- needs catalog "
                  "curation (entries + UPCs), then this watcher fills "
                  "their images")
    md += ["", f"## Older catalog gaps ({len(report['gaps'])}) "
           "-- triage separately, not daily news"]
    for s in report["gaps"][:40]:
        md.append(f"- {s['genre']}: {s['name']} "
                  f"(released {s.get('releaseDate') or '?'})")
    if len(report["gaps"]) > 40:
        md.append(f"- ... and {len(report['gaps']) - 40} more "
                  "(full list in new-set-watch-report.json)")
    if report.get("old_omitted"):
        md.append(f"- {report['old_omitted']} unmatched sets older than "
                  "5 years omitted (promo/back-catalog noise)")
    md += ["", f"## Tier-1 published ({len(report['tier1'])})"]
    for t in report["tier1"]:
        md.append(f"- {t['genre']}: {t['id']} -- {t['name']} "
                  f"(candidate: {t['candidate'][:80]})")
    md += ["", f"## Tier-2 needs eyes ({len(report['tier2'])})"]
    for t in report["tier2"]:
        md.append(f"- {t['genre']}: {t['id']} -- {t['name']} "
                  f"({t['reason'][:160]})")
    md += ["", "## Notes / degraded"]
    for n in report["notes"] + report["degraded"]:
        md.append(f"- {n}")
    if report.get("commit"):
        md.append(f"- commit: {report['commit']}")
    md_text = "\n".join(md) + "\n"
    with open(os.path.join(args.report_dir,
                           "new-set-watch-report.md"), "w") as f:
        f.write(md_text)
    with open(os.path.join(args.report_dir,
                           "new-set-watch-report.json"), "w") as f:
        json.dump(report, f, indent=2)
    print(md_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
