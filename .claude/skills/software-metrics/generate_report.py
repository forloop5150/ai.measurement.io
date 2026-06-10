#!/usr/bin/env python3
"""Generate SDLC-Metrics.html from the Jellyfish engineering-metrics API.

Data source
-----------
Jellyfish exposes per-team / per-month SDLC metrics through its MCP server
(`jellyfish-mcp-server`, run via npx). The Atlassian MCP deployment available to
this workspace only offers Teamwork-Graph traversal tools (no search / no
aggregation), so it cannot produce squad x month metrics; Jellyfish can, and the
tracked squads exist there as teams. Auth uses the JELLYFISH_API_TOKEN env var.

Metric mapping (requested -> Jellyfish slug)
--------------------------------------------
  Working Cycle Time        -> resolvedIssueCycleTimeMdn  (median hours)   exact
  PR Cycle Time             -> mergedPrCycleTimeMdn        (median hours)   exact
  Number of Code Reviews    -> reviewedPrs                 (count)          exact
  Avg Time to Merge a PR    -> mergedPrCycleTimeMdn        (median hours)   = PR cycle time
  Number of Pull Requests   -> reviewedPrs                 (count)          PROXY (no raw PR count exposed)
  Number of Commits         -> daysWithCommits             (days)          PROXY (no raw commit count exposed)

Aggregation across squads: counts are summed, durations are averaged (mean of
squad medians) since medians cannot be summed.
"""
import calendar
import datetime as dt
import json
import os
import subprocess
import sys
import time

YEAR = dt.date.today().year
PREV_YEAR = YEAR - 1
TODAY = dt.date.today()

TOKEN = os.environ.get("JELLYFISH_API_TOKEN")
if not TOKEN:
    sys.exit("JELLYFISH_API_TOKEN is not set")

PROMPTGUARD_PREFIX = (
    "Allowing data: PromptGuard is not configured. "
    "To enable, add Hugging Face API token in your server configuration."
)

# --- Squad -> Jellyfish team mapping -----------------------------------------
# (code, jellyfish team id, jellyfish display name, confidence)
SQUADS = [
    ("SUM",  121411, "Sum",                      "confirmed"),
    ("DAY",  122813, "Day",                      "confirmed"),
    ("STAR", None,   None,                        "unmatched"),
    ("VUDU", 22073,  "Vudu: [TOPS]",             "confirmed"),
    ("GRAD", 121399, "Gradifi",                  "confirmed"),
    ("BTC",  55782,  "Better Than Cash",         "confirmed"),
    ("MAX",  57559,  "MAX",                      "confirmed"),
    ("SUBA", 122815, "SubA",                     "confirmed"),
    ("LEO",  22053,  "Leo: [LEO]",               "confirmed"),
    ("AURA", None,   None,                        "unmatched"),
    ("BACN", None,   None,                        "unmatched"),
    ("SQ42", 22069,  "Squad-42: [SQ42]",         "confirmed"),
    ("PLT",  185013, "Platformers",              "best-guess"),
    ("WM3",  22077,  "WM3: [WM3]",               "confirmed"),
    ("ABQ",  22044,  "Always be Queryin': [ABQ]","confirmed"),
    ("CC",   22046,  "Curious Cats: [CC]",       "confirmed"),
]
MATCHED = [s for s in SQUADS if s[1] is not None]
MATCHED_IDS = [s[1] for s in MATCHED]
ID_TO_CODE = {s[1]: s[0] for s in MATCHED}

# --- Metric definitions ------------------------------------------------------
# agg: "sum" for counts, "avg" (mean of squad medians) for durations.
METRICS = [
    {"key": "workingCycleTime", "label": "Working Cycle Time",
     "slug": "resolvedIssueCycleTimeMdn", "unit": "hrs", "agg": "avg",
     "lower_better": True, "proxy": None},
    {"key": "prCycleTime", "label": "PR Cycle Time",
     "slug": "mergedPrCycleTimeMdn", "unit": "hrs", "agg": "avg",
     "lower_better": True, "proxy": None},
    {"key": "pullRequests", "label": "Number of Pull Requests",
     "slug": "reviewedPrs", "unit": "PRs", "agg": "sum",
     "lower_better": False,
     "proxy": "Jellyfish exposes no raw PR count; shown as Reviewed PRs (closest volume signal)."},
    {"key": "commits", "label": "Number of Commits",
     "slug": "daysWithCommits", "unit": "days", "agg": "sum",
     "lower_better": False,
     "proxy": "Jellyfish exposes no raw commit count; shown as Days-with-Commits."},
    {"key": "codeReviews", "label": "Number of Code Reviews",
     "slug": "reviewedPrs", "unit": "reviews", "agg": "sum",
     "lower_better": False, "proxy": None},
    {"key": "timeToMerge", "label": "Avg Time to Merge a PR",
     "slug": "mergedPrCycleTimeMdn", "unit": "hrs", "agg": "avg",
     "lower_better": True,
     "proxy": "Same as PR Cycle Time (median PR open->merge hours)."},
]


# --- Jellyfish MCP (stdio) client --------------------------------------------
class Jellyfish:
    def __init__(self):
        env = dict(os.environ)
        env["JELLYFISH_API_TOKEN"] = TOKEN
        self.p = subprocess.Popen(
            ["npx", "-y", "jellyfish-mcp-server@latest"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, text=True, bufsize=1)
        self._id = 0
        self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "sdlc-report", "version": "1"}})
        self._notify("notifications/initialized")

    def _write(self, msg):
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def _notify(self, method):
        self._write({"jsonrpc": "2.0", "method": method})

    def _rpc(self, method, params=None, timeout=180):
        self._id += 1
        mid = self._id
        self._write({"jsonrpc": "2.0", "method": method, "id": mid, "params": params or {}})
        start = time.time()
        while time.time() - start < timeout:
            line = self.p.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == mid:
                return msg
        raise TimeoutError(method)

    def tool_text(self, name, args):
        r = self._rpc("tools/call", {"name": name, "arguments": args})
        if "error" in r:
            raise RuntimeError(f"{name}: {r['error']}")
        content = r.get("result", {}).get("content", [])
        return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")

    def close(self):
        try:
            self.p.terminate()
        except Exception:
            pass


def parse_metrics(text):
    """Parse the Jellyfish MCP compact format into a list of records:
       {team_id, team, start, end, metrics:{slug: value-or-None}}."""
    text = text.replace(PROMPTGUARD_PREFIX, "")
    records, cur, in_metrics = [], None, False
    for raw in text.split("\n"):
        if not raw.strip():
            continue
        stripped = raw.strip()
        if stripped.startswith("- timeframe:"):
            if cur:
                records.append(cur)
            cur = {"team_id": None, "team": None, "start": None, "end": None, "metrics": {}}
            in_metrics = False
            continue
        if cur is None:
            continue
        if stripped.startswith("start:") and cur["start"] is None:
            cur["start"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("end:") and cur["end"] is None:
            cur["end"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("team_id:"):
            cur["team_id"] = int(stripped.split(":", 1)[1].strip())
        elif stripped.startswith("team:"):
            cur["team"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("metrics["):
            in_metrics = True
        elif in_metrics and "," in stripped:
            parts = stripped.rsplit(",", 3)
            if len(parts) == 4:
                _, slug, _unit, val = parts
                v = val.strip()
                try:
                    cur["metrics"][slug.strip()] = None if v in ("null", "") else float(v)
                except ValueError:
                    cur["metrics"][slug.strip()] = None
    if cur:
        records.append(cur)
    return records


def aggregate(values, how):
    """Aggregate a list of squad values (None-skipping)."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    if how == "sum":
        return sum(vals)
    return sum(vals) / len(vals)  # avg


def main():
    jf = Jellyfish()
    print("Fetching Jellyfish team metrics...", file=sys.stderr)

    # 1) 2026 monthly series for all matched squads
    monthly_txt = jf.tool_text("team_metrics", {
        "team_id": MATCHED_IDS, "start_date": f"{YEAR}-01-01",
        "end_date": f"{YEAR}-12-31", "unit": "month", "series": True})
    # 2) prior-year (full) aggregate per squad
    prev_txt = jf.tool_text("team_metrics", {
        "team_id": MATCHED_IDS, "start_date": f"{PREV_YEAR}-01-01",
        "end_date": f"{PREV_YEAR}-12-31", "series": False})
    # 3) current-year YTD aggregate per squad
    ytd_txt = jf.tool_text("team_metrics", {
        "team_id": MATCHED_IDS, "start_date": f"{YEAR}-01-01",
        "end_date": TODAY.isoformat(), "series": False})
    jf.close()

    monthly_recs = parse_metrics(monthly_txt)
    prev_recs = parse_metrics(prev_txt)
    ytd_recs = parse_metrics(ytd_txt)

    # monthly[code][month][slug] = value
    monthly = {}
    months_present = set()
    for rec in monthly_recs:
        code = ID_TO_CODE.get(rec["team_id"])
        if not code or not rec["start"]:
            continue
        month = int(rec["start"][5:7])
        months_present.add(month)
        monthly.setdefault(code, {})[month] = rec["metrics"]

    prev_year = {ID_TO_CODE[r["team_id"]]: r["metrics"]
                 for r in prev_recs if r["team_id"] in ID_TO_CODE}
    ytd = {ID_TO_CODE[r["team_id"]]: r["metrics"]
           for r in ytd_recs if r["team_id"] in ID_TO_CODE}

    months = sorted(m for m in months_present if m <= TODAY.month) or [TODAY.month]
    month_labels = [calendar.month_abbr[m] for m in months]

    # ---- Section A: all-squads monthly aggregate, per metric ----
    monthly_series = {}   # key -> [agg value per month]
    for met in METRICS:
        series = []
        for m in months:
            vals = [monthly.get(code, {}).get(m, {}).get(met["slug"])
                    for code, *_ in MATCHED]
            series.append(aggregate(vals, met["agg"]))
        monthly_series[met["key"]] = series

    # ---- Section B: yearly comparison (prev full year vs current YTD) ----
    yearly_cmp = {}   # key -> {prev, ytd}
    for met in METRICS:
        prev_vals = [prev_year.get(code, {}).get(met["slug"]) for code, *_ in MATCHED]
        ytd_vals = [ytd.get(code, {}).get(met["slug"]) for code, *_ in MATCHED]
        yearly_cmp[met["key"]] = {
            "prev": aggregate(prev_vals, met["agg"]),
            "ytd": aggregate(ytd_vals, met["agg"]),
        }

    # ---- Section C: per-squad tables (squad x month) per metric ----
    squad_tables = {}   # key -> list of {code, name, confidence, by_month, ytd}
    for met in METRICS:
        rows = []
        for code, tid, name, conf in SQUADS:
            by_month = [monthly.get(code, {}).get(m, {}).get(met["slug"]) for m in months]
            rows.append({
                "code": code, "name": name or "(no Jellyfish team)", "confidence": conf,
                "by_month": by_month,
                "ytd": ytd.get(code, {}).get(met["slug"]) if code in ytd else None,
            })
        squad_tables[met["key"]] = rows

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "year": YEAR, "prev_year": PREV_YEAR, "today": TODAY.isoformat(),
        "month_labels": month_labels,
        "metrics": METRICS,
        "monthly_series": monthly_series,
        "yearly_cmp": yearly_cmp,
        "squad_tables": squad_tables,
        "n_requested": len(SQUADS),
        "n_matched": len(MATCHED),
        "n_unmatched": len([s for s in SQUADS if s[1] is None]),
        "unmatched": [s[0] for s in SQUADS if s[1] is None],
        "best_guess": [s[0] for s in SQUADS if s[3] == "best-guess"],
    }

    html = render_html(payload)
    out_path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "SDLC-Metrics.html"))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {out_path}", file=sys.stderr)


def fmt(v, unit):
    if v is None:
        return "&mdash;"
    if unit in ("hrs", "days"):
        return f"{v:,.1f}"
    return f"{v:,.0f}"


def escape(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_html(p):
    metrics = p["metrics"]

    # KPI values
    cr = p["yearly_cmp"]["codeReviews"]
    wct = p["yearly_cmp"]["workingCycleTime"]
    prc = p["yearly_cmp"]["prCycleTime"]

    def delta_badge(key):
        c = p["yearly_cmp"][key]
        met = next(m for m in metrics if m["key"] == key)
        if c["prev"] in (None, 0) or c["ytd"] is None:
            return ""
        pct = (c["ytd"] - c["prev"]) / c["prev"] * 100
        good = (pct < 0) if met["lower_better"] else (pct > 0)
        cls = "up" if good else "down"
        arrow = "&#9650;" if pct >= 0 else "&#9660;"
        return f"<span class='delta {cls}'>{arrow} {abs(pct):.0f}% vs {p['prev_year']}</span>"

    # ---- per-metric monthly + yearly chart cards ----
    monthly_cards, yearly_cards, table_sections = [], [], []
    for met in metrics:
        k = met["key"]
        proxy = (f"<div class='proxy'>&#9888; Proxy &mdash; {escape(met['proxy'])}</div>"
                 if met["proxy"] else "")
        agg_note = "sum across squads" if met["agg"] == "sum" else "avg across squads"
        monthly_cards.append(
            f"<div class='panel'><h3>{escape(met['label'])} "
            f"<span class='u'>({met['unit']}, {agg_note})</span></h3>"
            f"{proxy}<canvas id='m_{k}'></canvas></div>")
        yearly_cards.append(
            f"<div class='panel'><h3>{escape(met['label'])} "
            f"<span class='u'>({met['unit']})</span></h3>{delta_badge(k)}"
            f"<canvas id='y_{k}'></canvas></div>")

        # table
        head = "".join(f"<th>{lbl}</th>" for lbl in p["month_labels"])
        body = []
        for row in p["squad_tables"][k]:
            cells = "".join(f"<td>{fmt(v, met['unit'])}</td>" for v in row["by_month"])
            badge = ""
            if row["confidence"] == "unmatched":
                badge = " <span class='tag warn'>unmatched</span>"
            elif row["confidence"] == "best-guess":
                badge = " <span class='tag guess'>best-guess</span>"
            body.append(
                f"<tr><td class='name'>{escape(row['code'])}{badge}"
                f"<div class='sub'>{escape(row['name'])}</div></td>"
                f"{cells}<td class='total'>{fmt(row['ytd'], met['unit'])}</td></tr>")
        table_sections.append(
            f"<div class='panel'><h3>{escape(met['label'])} by Squad &amp; Month "
            f"<span class='u'>({met['unit']})</span></h3>{proxy}"
            f"<div class='table-wrap'><table><thead><tr><th>Squad</th>{head}"
            f"<th>{p['year']} YTD</th></tr></thead><tbody>"
            f"{''.join(body)}</tbody></table></div></div>")

    # chart JS data
    chart_data = {
        "monthLabels": p["month_labels"],
        "monthly": {m["key"]: p["monthly_series"][m["key"]] for m in metrics},
        "yearly": {m["key"]: [p["yearly_cmp"][m["key"]]["prev"],
                              p["yearly_cmp"][m["key"]]["ytd"]] for m in metrics},
        "labels": {m["key"]: m["label"] for m in metrics},
        "prevYear": p["prev_year"], "year": p["year"],
    }

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SDLC Metrics &mdash; {p['year']}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg:#0f1115; --panel:#181b22; --border:#262a33; --text:#e7ebf3;
    --muted:#8a93a6; --accent:#5db5a8; --accent2:#7c9cff; --warn:#e0a458;
  }}
  * {{ box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--text); margin:0; padding:24px;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  h1 {{ margin:0 0 4px; font-size:24px; }}
  h2 {{ font-size:16px; margin:30px 0 14px; padding-bottom:8px;
    border-bottom:1px solid var(--border); color:var(--text); }}
  h3 {{ font-size:13px; margin:0 0 10px; font-weight:600; }}
  h3 .u {{ color:var(--muted); font-weight:400; font-size:11px; }}
  .subtitle {{ color:var(--muted); margin-bottom:18px; font-size:13px; }}
  .note {{ background:#1c1f27; border:1px solid var(--border); border-left:3px solid var(--accent);
    border-radius:8px; padding:12px 14px; color:var(--muted); font-size:12px; margin-bottom:20px; line-height:1.5; }}
  .note b {{ color:var(--text); }}
  .grid3 {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }}
  .panel {{ background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:16px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:8px; }}
  .kpi {{ background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:14px 16px; }}
  .kpi .label {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.05em; }}
  .kpi .value {{ font-size:23px; font-weight:600; margin-top:4px; }}
  .proxy {{ color:var(--warn); font-size:11px; margin:-4px 0 8px; line-height:1.4; }}
  .delta {{ font-size:11px; font-weight:600; }}
  .delta.up {{ color:#5db58a; }} .delta.down {{ color:#e07a7a; }}
  canvas {{ max-height:230px; }}
  table {{ width:100%; border-collapse:collapse; font-size:12px; }}
  th,td {{ text-align:right; padding:6px 9px; border-bottom:1px solid var(--border); white-space:nowrap; }}
  th:first-child, td:first-child {{ text-align:left; }}
  th {{ color:var(--muted); font-weight:500; background:#1c1f27; position:sticky; top:0; }}
  td.name {{ font-weight:600; }}
  td.name .sub {{ font-weight:400; color:var(--muted); font-size:10px; }}
  td.total {{ font-weight:600; color:var(--accent); }}
  .tag {{ font-size:9px; padding:1px 5px; border-radius:4px; vertical-align:middle; }}
  .tag.warn {{ background:#3a2a1a; color:var(--warn); }}
  .tag.guess {{ background:#1a2a3a; color:var(--accent2); }}
  .table-wrap {{ overflow-x:auto; }}
  @media(max-width:900px) {{ .grid3,.kpis {{ grid-template-columns:1fr 1fr; }} }}
</style>
</head>
<body>
  <h1>SDLC Metrics &mdash; {p['year']}</h1>
  <div class="subtitle">Generated {p['generated_at']} &bull; data through {p['today']} &bull; source: Jellyfish</div>

  <div class="note">
    <b>Data source:</b> Jellyfish engineering-metrics API (per-team, per-month).
    <b>Squads:</b> {p['n_matched']} of {p['n_requested']} matched to Jellyfish teams.
    <b>Unmatched (no data):</b> {escape(', '.join(p['unmatched'])) or 'none'}.
    <b>Best-guess mapping:</b> {escape(', '.join(p['best_guess'])) or 'none'} (PLT&rarr;Platformers).
    <br><b>Proxy metrics:</b> Jellyfish exposes no raw pull-request or commit counts &mdash;
    <i>Number of Pull Requests</i> uses Reviewed PRs and <i>Number of Commits</i> uses Days-with-Commits.
    Duration metrics are medians; aggregated across squads as an average (sum for counts).
  </div>

  <div class="kpis">
    <div class="kpi"><div class="label">Squads Tracked</div><div class="value">{p['n_matched']}<span style="font-size:13px;color:var(--muted)">/{p['n_requested']}</span></div></div>
    <div class="kpi"><div class="label">Code Reviews {p['year']} YTD</div><div class="value">{fmt(cr['ytd'],'reviews')}</div></div>
    <div class="kpi"><div class="label">Avg Working Cycle Time</div><div class="value">{fmt(wct['ytd'],'hrs')}<span style="font-size:13px;color:var(--muted)"> hrs</span></div></div>
    <div class="kpi"><div class="label">Avg PR Cycle Time</div><div class="value">{fmt(prc['ytd'],'hrs')}<span style="font-size:13px;color:var(--muted)"> hrs</span></div></div>
  </div>

  <h2>All Squads &mdash; Monthly Trend ({p['year']})</h2>
  <div class="grid3">{''.join(monthly_cards)}</div>

  <h2>Yearly Totals &mdash; {p['prev_year']} vs {p['year']} YTD</h2>
  <div class="grid3">{''.join(yearly_cards)}</div>

  <h2>Per-Squad Detail</h2>
  {''.join(table_sections)}

<script>
const D = {json.dumps(chart_data)};
const palette = ['#5db5a8','#7c9cff','#e0a458','#9b85ff','#e07a7a','#62d2a2'];
const gridc = '#262a33', tickc = '#8a93a6';
const baseScales = {{
  x: {{ ticks:{{color:tickc}}, grid:{{color:gridc}} }},
  y: {{ ticks:{{color:tickc, callback:v=>v.toLocaleString()}}, grid:{{color:gridc}}, beginAtZero:true }}
}};
const keys = Object.keys(D.labels);
keys.forEach((k, i) => {{
  const c = palette[i % palette.length];
  new Chart(document.getElementById('m_'+k), {{
    type:'bar',
    data:{{ labels:D.monthLabels, datasets:[{{ label:D.labels[k], data:D.monthly[k],
      backgroundColor:c, borderRadius:4 }}] }},
    options:{{ plugins:{{legend:{{display:false}},
      tooltip:{{callbacks:{{label:ctx=>ctx.parsed.y==null?'n/a':ctx.parsed.y.toLocaleString()}}}}}},
      scales:baseScales }}
  }});
  new Chart(document.getElementById('y_'+k), {{
    type:'bar',
    data:{{ labels:[String(D.prevYear), String(D.year)+' YTD'],
      datasets:[{{ label:D.labels[k], data:D.yearly[k],
        backgroundColor:['#3f4654', c], borderRadius:4 }}] }},
    options:{{ plugins:{{legend:{{display:false}},
      tooltip:{{callbacks:{{label:ctx=>ctx.parsed.y==null?'n/a':ctx.parsed.y.toLocaleString()}}}}}},
      scales:baseScales }}
  }});
}});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
