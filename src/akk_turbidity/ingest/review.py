"""Review artifacts shown in CircleCI before the approval step."""
import datetime
import json
from collections import Counter, defaultdict
from pathlib import Path

from . import layer

COLORS = {"Landsat": "#e6550d", "Sentinel2": "#3182bd", "PlanetScope": "#c51b8a"}
CONTEXT_DAYS = 90


def _summary_md(manifest, new):
    vectors = manifest["vectors"]
    by_status = Counter(v["status"] for v in vectors)
    last = manifest["base_latest_date"]

    groups = defaultdict(lambda: [0, 0.0])
    for f in new:
        p = f["properties"]
        g = groups[(p["Date"], p["Satelite"])]
        g[0] += 1
        g[1] += p["Area_ha"] or 0.0

    lines = [
        "# Turbidity outline update: review",
        "",
        f"- Prepared: {manifest['created_utc']}",
        f"- Current layer: {manifest['base_features']} features, latest date {last}",
        f"- **New outlines: {manifest['new_features']}** in {len(groups)} satellite/date group(s)",
        f"- Source files found: {len(vectors)} "
        + "(" + ", ".join(f"{n} {s}" for s, n in sorted(by_status.items())) + ")",
        "",
        "Status meanings: `new` = added below; `duplicate` = that satellite/date is already in the",
        "layer (only archived); `excluded` = listed in config/exclusions.txt; `empty` = GEE export",
        "has no polygons; `schema_mismatch` = not a Polygon layer, skipped as before. All of these",
        "are archived after publishing.",
        "",
    ]
    if groups:
        lines += ["## New outlines", "",
                  "| Date | Satellite | Polygons | Area (ha) | |",
                  "|---|---|---:|---:|---|"]
        for (date, sat), (n, ha) in sorted(groups.items()):
            flag = "backfill (older than current layer)" if last and date <= last else ""
            lines.append(f"| {date} | {sat} | {n} | {ha:,.1f} | {flag} |")
        lines.append("")
    else:
        lines += ["**No new outlines.** Approving will only archive the source files listed.", ""]

    filtered = [v for v in vectors if v["status"] == "new" and v["n_features"] == 0]
    odd = [v for v in vectors if v["status"] == "schema_mismatch"]
    if filtered or odd:
        lines += ["## Notes", ""]
        if filtered:
            lines.append(f"- {len(filtered)} new file(s) produced no outlines after the size filters "
                         "(details in manifest.json)")
        lines += [f"- `{v['name']}`: {v['status']}" for v in odd]
        lines.append("")
    if manifest["errors"]:
        lines += ["## Not processed (left in place, retried next run)", ""]
        lines += [f"- `{v['name']}`: {v['note']}" for v in manifest["errors"]]
        lines.append("")
    lines += [
        "## What approving does",
        "",
        f"1. Commits the candidate `{manifest['layer']}` to `main`, which AGOL reads.",
        "2. Gzips every source file listed in `manifest.json` into the archive prefix, verifies the",
        "   copy, then removes the original.",
        "",
        "Cancel the workflow instead if anything looks wrong. Nothing is changed until approval.",
        "To drop specific false positives for good, add `<date> <Satelite>` lines to",
        "`config/exclusions.txt` on main and re-run the ingest pipeline.",
    ]
    return "\n".join(lines) + "\n"


def _map_html(cfg, manifest, existing, new):
    last = manifest["base_latest_date"]
    context = []
    if last:
        cutoff = (datetime.date.fromisoformat(last) - datetime.timedelta(days=CONTEXT_DAYS)).isoformat()
        context = [f for f in existing if f["properties"]["Date"] > cutoff]
    roi_path = cfg.root / "config" / "akoakoa_turbidity_roi_simple.geojson"
    roi = json.loads(roi_path.read_text()) if roi_path.exists() else {"type": "FeatureCollection", "features": []}

    def js(obj):
        # safe to inline in <script>
        return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")

    fc = lambda feats: {"type": "FeatureCollection", "features": feats}
    title = f"Turbidity review: {manifest['new_features']} new outlines"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
 html,body{{margin:0;height:100%;font:14px system-ui,sans-serif}}
 #map{{position:absolute;top:0;bottom:0;left:0;right:320px}}
 #side{{position:absolute;top:0;bottom:0;right:0;width:320px;overflow:auto;padding:8px 12px;box-sizing:border-box;background:#fafafa;border-left:1px solid #ddd}}
 #side h1{{font-size:16px;margin:4px 0 8px}} #side li{{cursor:pointer;margin:2px 0}} #side li:hover{{text-decoration:underline}}
 .sw{{display:inline-block;width:10px;height:10px;margin-right:6px;border-radius:2px}}
 @media (max-width:700px){{#map{{right:0;bottom:40%}} #side{{top:60%;width:100%;border-left:0;border-top:1px solid #ddd}}}}
</style></head><body>
<div id="map"></div>
<div id="side"><h1>{title}</h1>
<div>Grey: layer outlines from the {CONTEXT_DAYS} days before {last}. Dashed: monitoring ROI.</div>
<div id="legend"></div><ul id="groups"></ul></div>
<script>
const NEW={js(fc(new))}, CTX={js(fc(context))}, ROI={js(roi)}, COLORS={js(COLORS)};
const map=L.map('map');
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',
  {{maxZoom:18,attribution:'Imagery &copy; Esri'}}).addTo(map);
const popup=f=>Object.entries(f.properties).map(([k,v])=>`<b>${{k}}</b>: ${{v}}`).join('<br>');
const roi=L.geoJSON(ROI,{{style:{{color:'#fff',weight:1,dashArray:'4 4',fill:false}}}}).addTo(map);
const ctx=L.geoJSON(CTX,{{style:{{color:'#bbb',weight:1,fillOpacity:0.15}},onEachFeature:(f,l)=>l.bindPopup(popup(f))}}).addTo(map);
const groups={{}};
const nw=L.geoJSON(NEW,{{style:f=>({{color:COLORS[f.properties.Satelite]||'#ff0',weight:2,fillOpacity:0.35}}),
  onEachFeature:(f,l)=>{{l.bindPopup(popup(f));const k=f.properties.Date+' '+f.properties.Satelite;(groups[k]=groups[k]||[]).push(l);}}}}).addTo(map);
L.control.layers(null,{{'New outlines':nw,'Recent layer':ctx,'ROI':roi}}).addTo(map);
document.getElementById('legend').innerHTML=Object.entries(COLORS).map(([k,c])=>`<span class="sw" style="background:${{c}}"></span>${{k}}`).join(' &nbsp; ');
const ul=document.getElementById('groups');
Object.keys(groups).sort().forEach(k=>{{const li=document.createElement('li');li.textContent=`${{k}} (${{groups[k].length}})`;
  li.onclick=()=>map.fitBounds(L.featureGroup(groups[k]).getBounds(),{{maxZoom:14}});ul.appendChild(li);}});
const b=NEW.features.length?nw.getBounds():roi.getBounds();
if(b.isValid()) map.fitBounds(b); else map.setView([19.6,-156.0],9);
</script></body></html>
"""


def write_review(cfg, review_dir, manifest, existing, new):
    review_dir = Path(review_dir)
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "summary.md").write_text(_summary_md(manifest, new))
    layer.write_layer(review_dir / "new_outlines.geojson", new, "new_outlines")
    (review_dir / "map.html").write_text(_map_html(cfg, manifest, existing, new))
