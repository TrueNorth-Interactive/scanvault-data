# ScanVault hosted data

Live data files for the ScanVault app (True North Interactive). The app
fetches these at runtime so new content lands **without an app release**;
the copies bundled in the APK are only a fallback (offline / first run).

| File | What it is | Updated when |
| --- | --- | --- |
| `sealed-products.json` | Sealed product catalog (ETBs, bundles, tins, boxes) for all TCGs | A new sealed product is announced/released |
| `pull-rates.json` | Community pull-rate odds per set (drives pull rates + Expected Box Value) | A set is ~3+ weeks post-release and opening data is solid |
| `drops-feed.json` | Curated upcoming-release calendar shown on Home | Release dates are announced or move |

Prices are never stored here — card and product values are fetched live
from the card databases by the app itself.

Maintained by the weekly ScanVault data refresh. Changes are committed
with the set/product they cover in the message.
