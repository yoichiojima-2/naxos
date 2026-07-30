"""Seed the lumen dataset with Lumen Works, Inc.'s fictional business data.

Loads via `bq load --replace`, which shares the gcloud CLI's credentials
(no ADC required) and makes re-runs idempotent. Generation is driven by a
fixed RNG seed, so output is stable for a given run date.
"""

import json
import os
import random
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = os.environ.get("GCLOUD_PROJECT_ID")
DATASET = "lumen"
FOUNDED = date(2021, 4, 1)
TODAY = datetime.now(ZoneInfo("Asia/Tokyo")).date()
USAGE_DAYS = 540
OUTAGE = (TODAY - timedelta(days=61), TODAY - timedelta(days=59))

rng = random.Random(7)

FIRST = ["Ava", "Liam", "Mia", "Noah", "Zoe", "Ethan", "Ivy", "Lucas", "Nora", "Owen",
         "Ruby", "Felix", "Isla", "Hugo", "Cora", "Jasper", "Luna", "Miles", "Elsie", "Theo"]
LAST = ["Bennett", "Carter", "Dawson", "Ellery", "Foster", "Gray", "Hale", "Ingram", "Jensen", "Keating",
        "Lambert", "Mercer", "Norwood", "Osei", "Parker", "Quinn", "Rowan", "Sutton", "Thorne", "Vale"]

DEPARTMENTS = [("D01", "Corporate", 5), ("D02", "Engineering", 14), ("D03", "Product", 7),
               ("D04", "Sales", 8), ("D05", "Customer Success", 8), ("D06", "Marketing", 6)]

PLANS = [("PL01", "Starter", 1200), ("PL02", "Pro", 2400), ("PL03", "Enterprise", 4800)]
UPGRADE = {"PL01": "PL02", "PL02": "PL03"}
PRICE = {plan_id: price for plan_id, _, price in PLANS}

NAME_A = ["Aster", "Beacon", "Cedar", "Delta", "Ember", "Fjord", "Grove", "Harbor", "Indigo", "Juniper",
          "Koto", "Larch", "Meridian", "Nimbus", "Orchid", "Pine", "Quartz", "Ridge", "Summit", "Terra"]
NAME_B = ["Labs", "Systems", "Digital", "Studio", "Partners", "Holdings", "Works", "Media", "Foods",
          "Logistics", "Design", "Consulting"]
NAME_C = ["", " Inc.", " K.K.", " Group"]
INDUSTRIES = ["software", "retail", "manufacturing", "media", "finance", "healthcare", "education", "construction"]

SEGMENTS = [("SMB", 60, (2, 15)), ("Mid", 30, (16, 60)), ("Enterprise", 10, (61, 200))]
INITIAL_PLAN = {"SMB": (["PL01", "PL02"], [70, 30]),
                "Mid": (["PL01", "PL02", "PL03"], [25, 60, 15]),
                "Enterprise": (["PL02", "PL03"], [40, 60])}

TICKET_RATE = {"SMB": 0.4, "Mid": 0.8, "Enterprise": 1.6}
TICKET_CATEGORIES = ["billing", "bug", "how-to", "feature-request", "performance"]
TICKET_WEIGHTS = [15, 25, 30, 20, 10]
RESOLUTION_HOURS = {"high": (2, 12), "medium": (6, 48), "low": (12, 120)}


def rand_date(lo: date, hi: date) -> date:
    return lo + timedelta(days=rng.randrange((hi - lo).days + 1))


def month_range(start: date, end: date):
    m = date(start.year, start.month, 1)
    while m <= end:
        yield m
        m = date(m.year + m.month // 12, m.month % 12 + 1, 1)


def build_departments_and_employees() -> tuple[list[dict], list[dict]]:
    employees, heads = [], {}
    idx = 0
    for dept_id, dept_name, headcount in DEPARTMENTS:
        for n in range(headcount):
            idx += 1
            employee_id = f"E{idx:03d}"
            title = "Head" if n == 0 else "Lead" if n == 1 else "Staff"
            hired_lo = FOUNDED if n == 0 else FOUNDED + timedelta(days=90)
            employees.append({
                "employee_id": employee_id,
                "name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                "department": dept_name,
                "title": title,
                "hired_date": rand_date(hired_lo, TODAY - timedelta(days=30)).isoformat(),
                "location": "Tokyo HQ" if rng.random() < 0.75 else "Remote",
            })
            if n == 0:
                heads[dept_id] = employee_id
    departments = [{"dept_id": d, "name": n, "head_employee_id": heads[d]} for d, n, _ in DEPARTMENTS]
    return departments, employees


def build_plans() -> list[dict]:
    return [{"plan_id": p, "name": n, "monthly_price_jpy_per_seat": price} for p, n, price in PLANS]


def build_accounts() -> list[dict]:
    combos = [f"{a} {b}{c}" for a in NAME_A for b in NAME_B for c in NAME_C]
    names = rng.sample(combos, 400)
    window = (TODAY - FOUNDED).days - 30
    accounts = []
    for i, company_name in enumerate(names):
        segment = rng.choices([s for s, _, _ in SEGMENTS], weights=[w for _, w, _ in SEGMENTS])[0]
        accounts.append({
            "account_id": f"A{i + 1:04d}",
            "company_name": company_name,
            "industry": rng.choice(INDUSTRIES),
            "segment": segment,
            "signup_date": (FOUNDED + timedelta(days=int(window * rng.random() ** 0.6))).isoformat(),
        })
    return accounts


def build_subscriptions(accounts: list[dict]) -> list[dict]:
    subscriptions = []

    def add(account_id: str, plan_id: str, seats: int, started: date, ended: date | None) -> None:
        row = {"subscription_id": f"S{len(subscriptions) + 1:04d}", "account_id": account_id,
               "plan_id": plan_id, "seats": seats, "started_date": started.isoformat()}
        if ended:
            row["ended_date"] = ended.isoformat()
        subscriptions.append(row)

    for account in accounts:
        signup = date.fromisoformat(account["signup_date"])
        seats_lo, seats_hi = next(r for s, _, r in SEGMENTS if s == account["segment"])
        seats = rng.randint(seats_lo, seats_hi)
        plans, weights = INITIAL_PLAN[account["segment"]]
        plan = rng.choices(plans, weights=weights)[0]
        churn = rand_date(signup + timedelta(days=60), signup + timedelta(days=900)) if rng.random() < 0.18 else None
        if churn and churn >= TODAY:
            churn = None
        tenure = (TODAY - signup).days
        if not churn and plan in UPGRADE and tenure > 240 and rng.random() < 0.25:
            upgrade = rand_date(signup + timedelta(days=120), TODAY - timedelta(days=60))
            add(account["account_id"], plan, seats, signup, upgrade)
            seats = round(seats * rng.uniform(1.2, 1.8))
            add(account["account_id"], UPGRADE[plan], seats, upgrade, None)
        else:
            add(account["account_id"], plan, seats, signup, churn)
    return subscriptions


def build_invoices(subscriptions: list[dict]) -> list[dict]:
    invoices = []
    for sub in subscriptions:
        start = date.fromisoformat(sub["started_date"])
        end = date.fromisoformat(sub["ended_date"]) if "ended_date" in sub else TODAY
        for month in month_range(start, end):
            invoices.append({
                "invoice_id": f"INV-{month:%Y%m}-{len(invoices) + 1:05d}",
                "account_id": sub["account_id"],
                "billing_month": month.isoformat(),
                "amount_jpy": sub["seats"] * PRICE[sub["plan_id"]],
                "status": "overdue" if rng.random() < 0.04 else "paid",
            })
    return invoices


def active_intervals(subscriptions: list[dict]) -> dict[str, list[tuple[date, date, int]]]:
    intervals = {}
    for sub in subscriptions:
        start = date.fromisoformat(sub["started_date"])
        end = date.fromisoformat(sub["ended_date"]) if "ended_date" in sub else TODAY
        intervals.setdefault(sub["account_id"], []).append((start, end, sub["seats"]))
    return intervals


def build_usage(subscriptions: list[dict]) -> list[dict]:
    intervals = active_intervals(subscriptions)
    usage = []
    start = TODAY - timedelta(days=USAGE_DAYS)
    for d in range(USAGE_DAYS + 1):
        day = start + timedelta(days=d)
        for account_id, spans in intervals.items():
            seats = next((s for lo, hi, s in spans if lo <= day <= hi), None)
            if seats is None:
                continue
            engagement = rng.uniform(0.55, 0.8) if day.weekday() < 5 else rng.uniform(0.1, 0.25)
            if OUTAGE[0] <= day <= OUTAGE[1]:
                engagement *= 0.3
            active_users = min(seats, max(0, round(seats * engagement)))
            usage.append({
                "usage_date": day.isoformat(),
                "account_id": account_id,
                "active_users": active_users,
                "projects_created": rng.randint(0, 2) if active_users else 0,
                "tasks_created": sum(rng.randint(1, 8) for _ in range(active_users)) if active_users else 0,
            })
    return usage


def build_tickets(accounts: list[dict], subscriptions: list[dict]) -> list[dict]:
    intervals = active_intervals(subscriptions)
    segment = {a["account_id"]: a["segment"] for a in accounts}
    tickets = []

    def add(account_id: str, day: date, category: str, priority: str) -> None:
        opened = datetime(day.year, day.month, day.day, rng.randint(9, 18), rng.randint(0, 59),
                          tzinfo=ZoneInfo("Asia/Tokyo"))
        lo, hi = RESOLUTION_HOURS[priority]
        row = {"ticket_id": f"T{len(tickets) + 1:05d}", "account_id": account_id,
               "opened_at": opened.isoformat(),
               "resolved_at": (opened + timedelta(hours=rng.uniform(lo, hi))).isoformat(),
               "category": category, "priority": priority}
        if rng.random() < 0.75:
            row["csat"] = rng.choices([5, 4, 3, 2, 1], weights=[45, 30, 12, 8, 5])[0]
        tickets.append(row)

    window_start = TODAY - timedelta(days=USAGE_DAYS)
    for account_id, spans in intervals.items():
        rate = TICKET_RATE[segment[account_id]]
        for month in month_range(window_start, TODAY):
            active_day = next((max(month, lo) for lo, hi, _ in spans if hi >= month and lo <= min(TODAY, date(
                month.year + month.month // 12, month.month % 12 + 1, 1) - timedelta(days=1))), None)
            if active_day is None:
                continue
            count = int(rate) + (1 if rng.random() < rate - int(rate) else 0)
            for _ in range(count):
                category = rng.choices(TICKET_CATEGORIES, weights=TICKET_WEIGHTS)[0]
                priority = rng.choices(["low", "medium", "high"], weights=[45, 40, 15])[0]
                add(account_id, min(rand_date(active_day, active_day + timedelta(days=27)), TODAY), category, priority)
        for d in range((OUTAGE[1] - OUTAGE[0]).days + 1):
            day = OUTAGE[0] + timedelta(days=d)
            if any(lo <= day <= hi for lo, hi, _ in spans) and rng.random() < 0.15:
                add(account_id, day, rng.choice(["performance", "bug"]), "high")
    return tickets


TABLES = {
    "departments": "dept_id:STRING,name:STRING,head_employee_id:STRING",
    "employees": "employee_id:STRING,name:STRING,department:STRING,title:STRING,hired_date:DATE,location:STRING",
    "plans": "plan_id:STRING,name:STRING,monthly_price_jpy_per_seat:INTEGER",
    "accounts": "account_id:STRING,company_name:STRING,industry:STRING,segment:STRING,signup_date:DATE",
    "subscriptions": "subscription_id:STRING,account_id:STRING,plan_id:STRING,seats:INTEGER,started_date:DATE,ended_date:DATE",
    "invoices": "invoice_id:STRING,account_id:STRING,billing_month:DATE,amount_jpy:INTEGER,status:STRING",
    "support_tickets": "ticket_id:STRING,account_id:STRING,opened_at:TIMESTAMP,resolved_at:TIMESTAMP,category:STRING,priority:STRING,csat:INTEGER",
    "usage_daily": "usage_date:DATE,account_id:STRING,active_users:INTEGER,projects_created:INTEGER,tasks_created:INTEGER",
}


def load(table: str, rows: list[dict], workdir: Path) -> None:
    path = workdir / f"{table}.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    cmd = ["bq"]
    if PROJECT:
        cmd += ["--project_id", PROJECT]
    cmd += ["load", "--replace", "--source_format=NEWLINE_DELIMITED_JSON"]
    if table == "usage_daily":
        cmd += ["--time_partitioning_type=DAY", "--time_partitioning_field=usage_date"]
    cmd += [f"{DATASET}.{table}", str(path), TABLES[table]]
    subprocess.run(cmd, check=True)
    print(f"{DATASET}.{table}: {len(rows)} rows")


def main() -> None:
    departments, employees = build_departments_and_employees()
    plans = build_plans()
    accounts = build_accounts()
    subscriptions = build_subscriptions(accounts)
    invoices = build_invoices(subscriptions)
    usage = build_usage(subscriptions)
    tickets = build_tickets(accounts, subscriptions)
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for table, rows in [("departments", departments), ("employees", employees), ("plans", plans),
                            ("accounts", accounts), ("subscriptions", subscriptions), ("invoices", invoices),
                            ("support_tickets", tickets), ("usage_daily", usage)]:
            load(table, rows, workdir)


if __name__ == "__main__":
    main()
