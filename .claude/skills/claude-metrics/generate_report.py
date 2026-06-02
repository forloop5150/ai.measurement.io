#!/usr/bin/env python3
"""Generate Claude-Metrics.html from the Anthropic Admin API.

Fetches the Claude Code usage report for the current year and renders a static
HTML dashboard with a per-month token bar chart, a per-model token pie chart, a
user x month token table, and a user x model token table.

The Anthropic Admin API exposes token counts (input + cache creation + cache
read + output), not raw request counts, so "tokens" is the tracked metric.
"""
import calendar
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
import re

API_BASE = "https://api.anthropic.com"
YEAR = dt.date.today().year
TODAY = dt.date.today()

API_KEY = os.environ.get("ANTHROPIC_ADMIN_KEY")
if not API_KEY:
    sys.exit("ANTHROPIC_ADMIN_KEY is not set")

HEADERS = {
    "x-api-key": API_KEY,
    "anthropic-version": "2023-06-01",
}

# Trailing -YYYYMMDD model date suffix, e.g. claude-haiku-4-5-20251001
DATE_SUFFIX = re.compile(r"-\d{8}$")
# Context-window annotation, e.g. claude-sonnet-4-6[1m]
CTX_SUFFIX = re.compile(r"\[[^\]]*\]$")


def get(path, params):
    url = API_BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def fetch_members():
    """email -> display name for every org user."""
    email_to_name = {}
    params = {"limit": 100}
    count = 0
    while True:
        data = get("/v1/organizations/users", params)
        for u in data.get("data", []):
            count += 1
            email = (u.get("email") or "").strip()
            name = (u.get("name") or "").strip()
            if email:
                email_to_name[email] = name if name else email
        if not data.get("has_more"):
            break
        params["after_id"] = data.get("last_id")
    return email_to_name, count


def normalize_model(model):
    """Collapse formatting variants of the same model to one canonical name.

    Merges dotted vs dashed versions (claude-sonnet-4.6 -> claude-sonnet-4-6),
    strips the [1m] context-window annotation, and drops -YYYYMMDD date suffixes
    so each underlying model is a single slice/column.
    """
    name = model or "unknown"
    name = CTX_SUFFIX.sub("", name)
    name = DATE_SUFFIX.sub("", name)
    name = name.replace(".", "-")
    return name


def actor_name(actor, email_to_name):
    """Resolve a usage record's actor to a human label (never an email)."""
    if actor.get("type") == "user_actor":
        email = actor.get("email_address") or ""
        if email:
            return email_to_name.get(email, email.split("@")[0])
        return "(unknown)"
    if actor.get("type") == "api_actor":
        return actor.get("api_key_name") or "(api key)"
    return "(unknown)"


def fetch_usage():
    """Yield every daily Claude Code usage record from Jan 1 of YEAR to today.

    The endpoint returns a single day's bucket per `starting_at`; it does not
    roll forward. Within a day, results are paged via the `page` cursor. So we
    iterate one day at a time and exhaust each day's pages.
    """
    day = dt.date(YEAR, 1, 1)
    while day <= TODAY:
        params = {"starting_at": day.isoformat(), "limit": 1000}
        n = 0
        while True:
            data = get("/v1/organizations/usage_report/claude_code", params)
            recs = data.get("data", [])
            n += len(recs)
            for rec in recs:
                yield rec
            if not data.get("has_more"):
                break
            params["page"] = data.get("next_page")
        if n:
            print(f"{day}: {n} records", file=sys.stderr)
        day += dt.timedelta(days=1)


def main():
    email_to_name, total_members = fetch_members()

    month_totals = {}        # month -> tokens
    model_totals = {}        # model -> tokens
    user_month = {}          # (name, month) -> tokens
    user_model = {}          # (name, model) -> tokens
    user_totals = {}         # name -> tokens
    models_seen = set()

    for rec in fetch_usage():
        date_str = rec.get("date", "")[:10]
        try:
            d = dt.date.fromisoformat(date_str)
        except ValueError:
            continue
        if d.year != YEAR:
            continue
        month = d.month
        name = actor_name(rec.get("actor", {}), email_to_name)
        for mb in rec.get("model_breakdown", []):
            model = normalize_model(mb.get("model"))
            t = mb.get("tokens", {})
            tokens = (
                (t.get("input") or 0)
                + (t.get("output") or 0)
                + (t.get("cache_read") or 0)
                + (t.get("cache_creation") or 0)
            )
            if tokens == 0:
                continue
            models_seen.add(model)
            month_totals[month] = month_totals.get(month, 0) + tokens
            model_totals[model] = model_totals.get(model, 0) + tokens
            user_month[(name, month)] = user_month.get((name, month), 0) + tokens
            user_model[(name, model)] = user_model.get((name, model), 0) + tokens
            user_totals[name] = user_totals.get(name, 0) + tokens

    # Months Jan..current month
    months_present = list(range(1, TODAY.month + 1))
    month_names = [calendar.month_name[m] for m in months_present]
    month_values = [month_totals.get(m, 0) for m in months_present]

    # Models sorted by total tokens desc
    sorted_models = [m for m, _ in sorted(model_totals.items(), key=lambda x: -x[1])]
    pie_labels = sorted_models
    pie_values = [model_totals[m] for m in sorted_models]

    # Users sorted by total tokens desc
    users = sorted(user_totals.keys(), key=lambda u: -user_totals[u])

    # User x month table
    month_rows = []
    for u in users:
        by_month = [user_month.get((u, m), 0) for m in months_present]
        month_rows.append({"name": u, "by_month": by_month, "total": sum(by_month)})
    month_col_totals = [sum(user_month.get((u, m), 0) for u in users) for m in months_present]
    grand_total = sum(month_col_totals)

    # User x model table
    model_rows = []
    for u in users:
        by_model = [user_model.get((u, m), 0) for m in sorted_models]
        model_rows.append({"name": u, "by_model": by_model, "total": sum(by_model)})
    model_col_totals = [sum(user_model.get((u, m), 0) for u in users) for m in sorted_models]

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "year": YEAR,
        "today": TODAY.isoformat(),
        "month_labels": month_names,
        "month_values": month_values,
        "pie_labels": pie_labels,
        "pie_values": pie_values,
        "model_labels": sorted_models,
        "month_rows": month_rows,
        "month_col_totals": month_col_totals,
        "model_rows": model_rows,
        "model_col_totals": model_col_totals,
        "grand_total": grand_total,
        "active_users": len(users),
        "total_members": total_members,
        "models_in_use": len(models_seen),
    }

    html = render_html(payload)
    out_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "Claude-Metrics.html")
    out_path = os.path.abspath(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {out_path}", file=sys.stderr)


def render_html(p):
    # User x month rows
    month_rows_html = []
    for r in p["month_rows"]:
        cells = "".join(f"<td>{v:,}</td>" for v in r["by_month"])
        month_rows_html.append(
            f"<tr><td class='name'>{escape(r['name'])}</td>{cells}"
            f"<td class='total'>{r['total']:,}</td></tr>"
        )
    month_rows_str = "\n".join(month_rows_html)
    month_header_cells = "".join(f"<th>{m}</th>" for m in p["month_labels"])
    month_footer_cells = "".join(f"<td>{v:,}</td>" for v in p["month_col_totals"])

    # User x model rows
    model_rows_html = []
    for r in p["model_rows"]:
        cells = "".join(f"<td>{v:,}</td>" for v in r["by_model"])
        model_rows_html.append(
            f"<tr><td class='name'>{escape(r['name'])}</td>{cells}"
            f"<td class='total'>{r['total']:,}</td></tr>"
        )
    model_rows_str = "\n".join(model_rows_html)
    model_header_cells = "".join(f"<th>{escape(m)}</th>" for m in p["model_labels"])
    model_footer_cells = "".join(f"<td>{v:,}</td>" for v in p["model_col_totals"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Claude Metrics &mdash; {p['year']}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0f1115;
    --panel: #181b22;
    --border: #262a33;
    --text: #e7ebf3;
    --muted: #8a93a6;
    --accent: #d4a373;
  }}
  * {{ box-sizing: border-box; }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 24px; }}
  h1 {{ margin: 0 0 4px; font-size: 24px; }}
  .subtitle {{ color: var(--muted); margin-bottom: 24px; font-size: 13px; }}
  .note {{ color: var(--muted); font-size: 12px; margin-bottom: 16px; font-style: italic; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }}
  .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 18px; margin-bottom: 20px; }}
  .panel h2 {{ font-size: 15px; margin: 0 0 12px; color: var(--text); font-weight: 600; }}
  .kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 20px; }}
  .kpi {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }}
  .kpi .label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }}
  .kpi .value {{ font-size: 24px; font-weight: 600; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: right; padding: 8px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }}
  th:first-child, td:first-child {{ text-align: left; }}
  th {{ color: var(--muted); font-weight: 500; background: #1c1f27; position: sticky; top: 0; }}
  td.name {{ font-weight: 500; }}
  td.total {{ font-weight: 600; color: var(--accent); }}
  tfoot td {{ font-weight: 600; border-top: 2px solid var(--border); border-bottom: none; }}
  .table-wrap {{ max-height: 520px; overflow-y: auto; overflow-x: auto; }}
  canvas {{ max-height: 360px; }}
</style>
</head>
<body>
  <h1>Claude Metrics &mdash; {p['year']}</h1>
  <div class="subtitle">Generated {p['generated_at']} &bull; data through {p['today']}</div>
  <div class="note">Note: the Anthropic Admin API reports token usage rather than raw request counts; totals shown reflect total tokens (input + cache creation + cache read + output) per the Claude Code usage report.</div>

  <div class="kpis">
    <div class="kpi"><div class="label">Total Tokens</div><div class="value">{p['grand_total']:,}</div></div>
    <div class="kpi"><div class="label">Active Users</div><div class="value">{p['active_users']}</div></div>
    <div class="kpi"><div class="label">Team Members</div><div class="value">{p['total_members']}</div></div>
    <div class="kpi"><div class="label">Models In Use</div><div class="value">{p['models_in_use']}</div></div>
  </div>

  <div class="grid">
    <div class="panel">
      <h2>Tokens by Month</h2>
      <canvas id="monthChart"></canvas>
    </div>
    <div class="panel">
      <h2>Tokens by Model</h2>
      <canvas id="modelChart"></canvas>
    </div>
  </div>

  <div class="panel">
    <h2>Tokens per User by Month</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>Name</th>{month_header_cells}<th>Total</th></tr>
        </thead>
        <tbody>
{month_rows_str}
        </tbody>
        <tfoot>
          <tr><td>Total</td>{month_footer_cells}<td>{p['grand_total']:,}</td></tr>
        </tfoot>
      </table>
    </div>
  </div>

  <div class="panel">
    <h2>Tokens per User by Model</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>Name</th>{model_header_cells}<th>Total</th></tr>
        </thead>
        <tbody>
{model_rows_str}
        </tbody>
        <tfoot>
          <tr><td>Total</td>{model_footer_cells}<td>{p['grand_total']:,}</td></tr>
        </tfoot>
      </table>
    </div>
  </div>

<script>
const monthData = {json.dumps({"labels": p["month_labels"], "values": p["month_values"]})};
const modelData = {json.dumps({"labels": p["pie_labels"], "values": p["pie_values"]})};

const palette = [
  '#d4a373', '#7c9cff', '#9b85ff', '#5db5a8', '#e07a7a',
  '#f0c674', '#a3c585', '#c78bd9', '#6ab0d8', '#e89c5a',
  '#b07e5d', '#5a7fbf'
];

new Chart(document.getElementById('monthChart'), {{
  type: 'bar',
  data: {{
    labels: monthData.labels,
    datasets: [{{
      label: 'Total Tokens',
      data: monthData.values,
      backgroundColor: '#d4a373',
      borderRadius: 4,
    }}]
  }},
  options: {{
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ color: '#8a93a6' }}, grid: {{ color: '#262a33' }} }},
      y: {{ ticks: {{ color: '#8a93a6', callback: v => v.toLocaleString() }}, grid: {{ color: '#262a33' }} }}
    }}
  }}
}});

new Chart(document.getElementById('modelChart'), {{
  type: 'pie',
  data: {{
    labels: modelData.labels,
    datasets: [{{
      data: modelData.values,
      backgroundColor: palette.slice(0, modelData.labels.length),
      borderColor: '#0f1115',
      borderWidth: 2,
    }}]
  }},
  options: {{
    plugins: {{
      legend: {{ position: 'right', labels: {{ color: '#e7ebf3', font: {{ size: 11 }} }} }},
      tooltip: {{ callbacks: {{ label: ctx => `${{ctx.label}}: ${{ctx.parsed.toLocaleString()}}` }} }}
    }}
  }}
}});
</script>
</body>
</html>
"""


def escape(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


if __name__ == "__main__":
    main()
