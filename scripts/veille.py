"""Collecte des données de veille pour youssouf55.github.io.

Lancé par GitHub Actions (.github/workflows/veille.yml) toutes les 6 heures.
Les appels aux sources se font ici, côté serveur : le navigateur des visiteurs
ne lit que data/veille.json, servi par le site lui-même.

Sources
- FMI, PortWatch : passages quotidiens de navires dans les détroits (signaux AIS).
- BCE : cours de référence euro-dollar.
- EIA (États-Unis) : prix spot du Brent, fichier public sans clé.

Si une source ne répond pas, on garde la dernière version connue.
"""
import csv
import datetime as dt
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request

START = "2023-01-01"
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "veille.json")
UA = "Mozilla/5.0 (compatible; veille-youssouf55/1.0; +https://youssouf55.github.io/)"

PORTWATCH = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
             "Daily_Chokepoints_Data/FeatureServer/0/query")
STRAITS = {
    "hormuz": "chokepoint6",
    "bab": "chokepoint4",
    "suez": "chokepoint1",
    "cape": "chokepoint7",
}
ECB = ("https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"
       "?startPeriod=" + START + "&format=csvdata")
EIA_XLS = "https://www.eia.gov/dnav/pet/hist_xls/RBRTEd.xls"


def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # réseau, 5xx...
            last = e
            time.sleep(4 * (i + 1))
    raise last


def portwatch(port_id):
    rows, offset = [], 0
    while True:
        q = urllib.parse.urlencode({
            "where": f"portid='{port_id}' AND date>=date '{START}'",
            "outFields": "date,n_tanker,n_total",
            "orderByFields": "date",
            "returnGeometry": "false",
            "resultOffset": offset,
            "resultRecordCount": 1000,
            "f": "json",
        })
        j = json.loads(get(PORTWATCH + "?" + q))
        if "error" in j:
            raise RuntimeError(j["error"])
        feats = j.get("features", [])
        rows += [f["attributes"] for f in feats]
        if not j.get("exceededTransferLimit") or not feats:
            break
        offset += len(feats)
    if not rows:
        raise RuntimeError("aucune ligne")
    # série quotidienne continue ; un jour manquant vaut None
    by_day = {r["date"][:10]: r for r in rows}
    d = dt.date.fromisoformat(rows[0]["date"][:10])
    end = dt.date.fromisoformat(rows[-1]["date"][:10])
    tanker, total = [], []
    while d <= end:
        r = by_day.get(d.isoformat())
        tanker.append(None if r is None else r["n_tanker"])
        total.append(None if r is None else r["n_total"])
        d += dt.timedelta(days=1)
    return {"start": rows[0]["date"][:10], "end": end.isoformat(), "tanker": tanker, "total": total}


def ecb_eurusd():
    text = get(ECB).decode("utf-8")
    rd = csv.DictReader(io.StringIO(text))
    pts = [[r["TIME_PERIOD"], round(float(r["OBS_VALUE"]), 4)] for r in rd if r.get("OBS_VALUE")]
    if len(pts) < 100:
        raise RuntimeError("série trop courte")
    return {"points": pts}


def eia_brent():
    import xlrd  # installé par le workflow
    book = xlrd.open_workbook(file_contents=get(EIA_XLS))
    sh = book.sheet_by_name("Data 1")
    pts = []
    for i in range(sh.nrows):
        a, b = sh.cell_value(i, 0), sh.cell_value(i, 1)
        if isinstance(a, float) and isinstance(b, float):
            d = xlrd.xldate_as_datetime(a, book.datemode).date()
            if d.isoformat() >= START:
                pts.append([d.isoformat(), round(b, 2)])
    if len(pts) < 100:
        raise RuntimeError("série trop courte")
    return {"points": pts}


def main():
    try:
        old = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        old = {}
    out = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "straits": dict(old.get("straits", {})),
           "eurusd": old.get("eurusd"), "brent": old.get("brent"), "errors": []}
    for key, pid in STRAITS.items():
        try:
            out["straits"][key] = portwatch(pid)
        except Exception as e:
            out["errors"].append(f"portwatch {key}: {e}")
    for key, fn in (("eurusd", ecb_eurusd), ("brent", eia_brent)):
        try:
            out[key] = fn()
        except Exception as e:
            out["errors"].append(f"{key}: {e}")
    strip = lambda d: {k: v for k, v in d.items() if k != "generated"}
    if old and strip(old) == strip(out):
        print("aucun changement depuis", old.get("generated"))
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("ok", out["generated"], "erreurs:", out["errors"] or "aucune")
    # échec seulement si rien d'utilisable
    if not out["straits"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
