#!/usr/bin/env python3
"""Generate Cursor-Metrics.html from the Cursor Admin API.

Fetches monthly usage events for the current year and renders a static HTML
dashboard with a per-month bar chart, per-model pie chart, and a user x month
table of requests.
"""
import calendar
import datetime as dt
import json
import os
import sys
import urllib.request
import urllib.error
import base64

API_BASE = "https://api.cursor.com"
YEAR = dt.date.today().year
TODAY = dt.date.today()

API_KEY = os.environ.get("CURSOR_API_KEY")
if not API_KEY:
    sys.exit("CURSOR_API_KEY is not set")

AUTH = "Basic " + base64.b64encode(f"{API_KEY}:".encode()).decode()


def post(path, body):
    req = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(body).encode(),
        headers={"Authorization": AUTH, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def get(path):
    req = urllib.request.Request(
        API_BASE + path,
        headers={"Authorization": AUTH},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def ms(date):
    """Convert date to epoch ms (UTC midnight)."""
    return int(dt.datetime(date.year, date.month, date.day, tzinfo=dt.timezone.utc).timestamp() * 1000)


def month_ranges(year, today):
    """Yield (month_index 1-12, start_date, end_date) for each month of year up to today.

    API caps ranges at 30 days, so months are split into two halves.
    """
    for m in range(1, 13):
        first = dt.date(year, m, 1)
        if first > today:
            break
        last_day = calendar.monthrange(year, m)[1]
        last = min(dt.date(year, m, last_day), today)
        if (last - first).days <= 29:
            yield m, first, last, "full"
        else:
            mid = first + dt.timedelta(days=14)
            yield m, first, mid, "first-half"
            yield m, mid + dt.timedelta(days=1), last, "second-half"


def fetch_events(start_date, end_date):
    """Fetch all filtered usage events between start and end inclusive."""
    events = []
    page = 1
    while True:
        body = {
            "startDate": ms(start_date),
            "endDate": ms(end_date + dt.timedelta(days=1)) - 1,
            "page": page,
            "pageSize": 1000,
        }
        data = post("/teams/filtered-usage-events", body)
        events.extend(data.get("usageEvents", []))
        if not data.get("pagination", {}).get("hasNextPage"):
            break
        page += 1
    return events


def fetch_daily(start_date, end_date):
    body = {
        "startDate": ms(start_date),
        "endDate": ms(end_date + dt.timedelta(days=1)) - 1,
    }
    data = post("/teams/daily-usage-data", body)
    return data.get("data", [])


def main():
    members = get("/teams/members").get("teamMembers", [])
    email_to_name = {}
    for m in members:
        name = (m.get("name") or "").strip()
        email = m.get("email", "")
        email_to_name[email] = name if name else email

    month_totals = {}        # month -> total requests
    model_totals = {}        # model -> total requests
    user_month_totals = {}   # (email, month) -> total requests
    user_emails = set()

    for month, start, end, _ in month_ranges(YEAR, TODAY):
        print(f"Fetching {start} to {end}", file=sys.stderr)
        events = fetch_events(start, end)
        for e in events:
            email = e.get("userEmail") or "(unknown)"
            user_emails.add(email)
            model = e.get("model") or "unknown"
            month_totals[month] = month_totals.get(month, 0) + 1
            model_totals[model] = model_totals.get(model, 0) + 1
            user_month_totals[(email, month)] = user_month_totals.get((email, month), 0) + 1

    months_present = sorted(month_totals.keys())
    month_names = [calendar.month_name[m] for m in months_present]
    month_values = [month_totals[m] for m in months_present]

    # Sort models by usage desc; group small ones into "other" for pie clarity
    sorted_models = sorted(model_totals.items(), key=lambda x: -x[1])
    total_model_requests = sum(model_totals.values()) or 1
    pie_labels = []
    pie_values = []
    other_total = 0
    for name, count in sorted_models:
        if count / total_model_requests < 0.01 and len(pie_labels) >= 6:
            other_total += count
        else:
            pie_labels.append(name)
            pie_values.append(count)
    if other_total > 0:
        pie_labels.append("other")
        pie_values.append(other_total)

    # Build user/month table: only include users with any requests
    users_with_data = sorted(
        [u for u in user_emails if any(user_month_totals.get((u, m), 0) for m in months_present)],
        key=lambda u: -sum(user_month_totals.get((u, m), 0) for m in months_present),
    )
    table_rows = []
    for email in users_with_data:
        name = email_to_name.get(email, email)
        row = {
            "name": name,
            "email": email,
            "by_month": [user_month_totals.get((email, m), 0) for m in months_present],
        }
        row["total"] = sum(row["by_month"])
        table_rows.append(row)

    column_totals = [sum(user_month_totals.get((u, m), 0) for u in users_with_data) for m in months_present]
    grand_total = sum(column_totals)

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "year": YEAR,
        "today": TODAY.isoformat(),
        "month_labels": month_names,
        "month_values": month_values,
        "pie_labels": pie_labels,
        "pie_values": pie_values,
        "table_rows": table_rows,
        "column_totals": column_totals,
        "grand_total": grand_total,
        "active_users": len(users_with_data),
        "total_members": len(members),
    }

    html = render_html(payload)
    out_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "Cursor-Metrics.html")
    out_path = os.path.abspath(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {out_path}", file=sys.stderr)


def render_html(p):
    rows_html = []
    for r in p["table_rows"]:
        cells = "".join(f"<td>{v:,}</td>" for v in r["by_month"])
        rows_html.append(
            f"<tr><td class='name'>{escape(r['name'])}</td>"
            f"<td class='email'>{escape(r['email'])}</td>"
            f"{cells}<td class='total'>{r['total']:,}</td></tr>"
        )
    rows_str = "\n".join(rows_html)

    month_header_cells = "".join(f"<th>{m}</th>" for m in p["month_labels"])
    footer_cells = "".join(f"<td>{v:,}</td>" for v in p["column_totals"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cursor Metrics — {p['year']}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0f1115;
    --panel: #181b22;
    --border: #262a33;
    --text: #e7ebf3;
    --muted: #8a93a6;
    --accent: #7c9cff;
  }}
  * {{ box-sizing: border-box; }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 24px; }}
  h1 {{ margin: 0 0 4px; font-size: 24px; }}
  .subtitle {{ color: var(--muted); margin-bottom: 24px; font-size: 13px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }}
  .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 18px; }}
  .panel h2 {{ font-size: 15px; margin: 0 0 12px; color: var(--text); font-weight: 600; }}
  .kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 20px; }}
  .kpi {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }}
  .kpi .label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }}
  .kpi .value {{ font-size: 24px; font-weight: 600; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: right; padding: 8px 10px; border-bottom: 1px solid var(--border); }}
  th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
  th {{ color: var(--muted); font-weight: 500; background: #1c1f27; position: sticky; top: 0; }}
  td.name {{ font-weight: 500; }}
  td.email {{ color: var(--muted); }}
  td.total {{ font-weight: 600; color: var(--accent); }}
  tfoot td {{ font-weight: 600; border-top: 2px solid var(--border); border-bottom: none; }}
  .table-wrap {{ max-height: 520px; overflow-y: auto; }}
  canvas {{ max-height: 360px; }}
</style>
</head>
<body>
  <h1>Cursor Metrics — {p['year']}</h1>
  <div class="subtitle">Generated {p['generated_at']} • data through {p['today']}</div>

  <div class="kpis">
    <div class="kpi"><div class="label">Total Requests</div><div class="value">{p['grand_total']:,}</div></div>
    <div class="kpi"><div class="label">Active Users</div><div class="value">{p['active_users']}</div></div>
    <div class="kpi"><div class="label">Team Members</div><div class="value">{p['total_members']}</div></div>
    <div class="kpi"><div class="label">Models In Use</div><div class="value">{len(p['pie_labels'])}</div></div>
  </div>

  <div class="grid">
    <div class="panel">
      <h2>Requests by Month</h2>
      <canvas id="monthChart"></canvas>
    </div>
    <div class="panel">
      <h2>Requests by Model</h2>
      <canvas id="modelChart"></canvas>
    </div>
  </div>

  <div class="panel">
    <h2>Requests per User by Month</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>Name</th><th>Email</th>{month_header_cells}<th>Total</th></tr>
        </thead>
        <tbody>
          {rows_str}
        </tbody>
        <tfoot>
          <tr><td>Total</td><td></td>{footer_cells}<td>{p['grand_total']:,}</td></tr>
        </tfoot>
      </table>
    </div>
  </div>

<script>
const monthData = {json.dumps({"labels": p["month_labels"], "values": p["month_values"]})};
const modelData = {json.dumps({"labels": p["pie_labels"], "values": p["pie_values"]})};

const palette = ["#7c9cff","#62d2a2","#ffb86b","#f06bb3","#9b8cff","#5fc7d6","#ffd86b","#ff7a7a","#a3e635","#c084fc","#fb923c","#34d399"];

new Chart(document.getElementById("monthChart"), {{
  type: "bar",
  data: {{ labels: monthData.labels, datasets: [{{
    label: "Requests",
    data: monthData.values,
    backgroundColor: "#7c9cff",
    borderRadius: 6,
  }}]}},
  options: {{
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ color: "#8a93a6" }}, grid: {{ color: "#262a33" }} }},
      y: {{ ticks: {{ color: "#8a93a6" }}, grid: {{ color: "#262a33" }}, beginAtZero: true }}
    }}
  }}
}});

new Chart(document.getElementById("modelChart"), {{
  type: "doughnut",
  data: {{ labels: modelData.labels, datasets: [{{
    data: modelData.values,
    backgroundColor: modelData.labels.map((_, i) => palette[i % palette.length]),
    borderColor: "#181b22",
    borderWidth: 2,
  }}]}},
  options: {{
    plugins: {{
      legend: {{ position: "right", labels: {{ color: "#e7ebf3", boxWidth: 12 }} }}
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
