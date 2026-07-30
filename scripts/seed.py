"""Seed the soramame dataset with Soramame Inc.'s fictional business data.

Loads via `bq load --replace`, which shares the gcloud CLI's credentials
(no ADC required) and makes re-runs idempotent. Generation is driven by a
fixed RNG seed, so output is stable for a given run date.
"""

import bisect
import json
import os
import random
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = os.environ.get("GCLOUD_PROJECT_ID")
DATASET = "soramame"
FOUNDED = date(2021, 4, 1)
TODAY = datetime.now(ZoneInfo("Asia/Tokyo")).date()
ORDER_DAYS = 540

rng = random.Random(7)

SURNAMES = ["佐藤", "鈴木", "高橋", "田中", "伊藤", "渡辺", "山本", "中村", "小林", "加藤",
            "吉田", "山田", "佐々木", "山口", "松本", "井上", "木村", "林", "斎藤", "清水"]
ROMAJI = ["sato", "suzuki", "takahashi", "tanaka", "ito", "watanabe", "yamamoto", "nakamura",
          "kobayashi", "kato", "yoshida", "yamada", "sasaki", "yamaguchi", "matsumoto",
          "inoue", "kimura", "hayashi", "saito", "shimizu"]
GIVEN = ["陽菜", "蓮", "結衣", "湊", "葵", "樹", "咲良", "悠真", "芽依", "朝陽",
         "凛", "蒼", "詩", "碧", "紬", "律", "楓", "陽翔", "澪", "颯"]

CITIES = [("世田谷区", "東京都"), ("杉並区", "東京都"), ("目黒区", "東京都"), ("武蔵野市", "東京都"),
          ("横浜市", "神奈川県"), ("川崎市", "神奈川県"), ("さいたま市", "埼玉県"), ("千葉市", "千葉県"),
          ("大阪市", "大阪府"), ("吹田市", "大阪府"), ("京都市", "京都府"), ("神戸市", "兵庫県"),
          ("名古屋市", "愛知県"), ("福岡市", "福岡県"), ("札幌市", "北海道"), ("仙台市", "宮城県")]
CITY_WEIGHTS = [10, 8, 7, 5, 9, 6, 5, 4, 8, 4, 5, 4, 4, 3, 2, 2]

CATALOG = {
    "kitchen": (["まな板", "保存容器", "キッチンクロス", "木べら", "ボウル", "水切りかご", "エプロン", "スポンジ"],
                ["竹製", "ひのき", "再生ガラス", "オーガニックコットン", "ステンレス", "琺瑯"], (800, 4500)),
    "bath": (["バスタオル", "フェイスタオル", "石けん", "シャンプーバー", "バスマット", "歯ブラシ"],
             ["オーガニックコットン", "竹炭", "無添加", "麻"], (500, 3800)),
    "stationery": (["ノート", "ペンケース", "メモパッド", "カレンダー", "ボールペン", "クリップ"],
                   ["再生紙", "バンブー", "コルク", "リサイクルPET"], (300, 2200)),
    "textiles": (["ブランケット", "クッションカバー", "ラグ", "カーテン", "トートバッグ", "ルームソックス"],
                 ["リネン", "オーガニックコットン", "ウール", "再生ポリエステル"], (1500, 12000)),
}

DEPARTMENTS = [("D01", "経営企画", 5), ("D02", "EC事業部", 12), ("D03", "商品企画", 8),
               ("D04", "CS", 9), ("D05", "物流", 8), ("D06", "コーポレートIT", 6)]

CHANNELS = ["web", "app", "marketplace", "wholesale"]
CHANNEL_WEIGHTS = [45, 25, 22, 8]


def rand_date(lo: date, hi: date) -> date:
    return lo + timedelta(days=rng.randrange((hi - lo).days + 1))


def person_name() -> tuple[str, str]:
    i = rng.randrange(len(SURNAMES))
    return f"{SURNAMES[i]} {rng.choice(GIVEN)}", ROMAJI[i]


def build_departments_and_employees() -> tuple[list[dict], list[dict]]:
    employees, heads = [], {}
    idx = 0
    for dept_id, dept_name, headcount in DEPARTMENTS:
        for n in range(headcount):
            idx += 1
            employee_id = f"E{idx:03d}"
            name, _ = person_name()
            title = "部長" if n == 0 else "リーダー" if n == 1 else "スタッフ"
            hired_lo = FOUNDED if n == 0 else FOUNDED + timedelta(days=90)
            location = "千葉物流センター" if dept_name == "物流" and n >= 2 else "東京本社"
            employees.append({
                "employee_id": employee_id,
                "name": name,
                "department": dept_name,
                "title": title,
                "hired_date": rand_date(hired_lo, TODAY - timedelta(days=30)).isoformat(),
                "location": location,
            })
            if n == 0:
                heads[dept_id] = employee_id
    departments = [{"dept_id": d, "name": n, "head_employee_id": heads[d]} for d, n, _ in DEPARTMENTS]
    return departments, employees


def build_products() -> list[dict]:
    products = []
    categories = list(CATALOG)
    for i in range(80):
        category = categories[i % len(categories)]
        items, materials, (lo, hi) = CATALOG[category]
        products.append({
            "product_id": f"P{i + 1:03d}",
            "name": f"{rng.choice(materials)} {rng.choice(items)} #{i + 1:03d}",
            "category": category,
            "unit_price_jpy": rng.randrange(lo, hi + 1, 10),
            "launched_date": rand_date(FOUNDED, TODAY - timedelta(days=30)).isoformat(),
        })
    return products


def build_customers() -> list[dict]:
    customers = []
    for i in range(500):
        name, romaji = person_name()
        city, prefecture = rng.choices(CITIES, weights=CITY_WEIGHTS)[0]
        customers.append({
            "customer_id": f"C{i + 1:04d}",
            "name": name,
            "email": f"{romaji}.{i + 1:04d}@example.com",
            "city": city,
            "prefecture": prefecture,
            "signup_date": rand_date(FOUNDED, TODAY - timedelta(days=7)).isoformat(),
        })
    customers.sort(key=lambda c: c["signup_date"])
    return customers


def order_status(day: date) -> str:
    age = (TODAY - day).days
    if age < 3:
        return "processing"
    if age < 7:
        return "shipped"
    roll = rng.random()
    if roll < 0.03:
        return "cancelled"
    if roll < 0.045:
        return "refunded"
    return "delivered"


def build_orders(customers: list[dict], products: list[dict]) -> tuple[list[dict], list[dict]]:
    signup_dates = [c["signup_date"] for c in customers]
    orders, order_items = [], []
    start = TODAY - timedelta(days=ORDER_DAYS)
    for d in range(ORDER_DAYS + 1):
        day = start + timedelta(days=d)
        base = 4 + 10 * d / ORDER_DAYS
        mult = 1.0
        if day.weekday() >= 5:
            mult *= 1.35
        if day.month in (11, 12):
            mult *= 1.5
        if day.month == 1 and day.day <= 7:
            mult *= 0.6
        eligible = bisect.bisect_right(signup_dates, day.isoformat())
        if eligible == 0:
            continue
        for seq in range(max(0, round(rng.gauss(base * mult, 2)))):
            order_id = f"ORD-{day:%Y%m%d}-{seq + 1:04d}"
            channel = rng.choices(CHANNELS, weights=CHANNEL_WEIGHTS)[0]
            orders.append({
                "order_id": order_id,
                "customer_id": customers[rng.randrange(eligible)]["customer_id"],
                "order_date": day.isoformat(),
                "status": order_status(day),
                "channel": channel,
            })
            wholesale = channel == "wholesale"
            for product in rng.sample(products, rng.randint(3, 6) if wholesale else rng.choices([1, 2, 3, 4], weights=[45, 30, 17, 8])[0]):
                quantity = rng.randint(10, 40) if wholesale else rng.randint(1, 3)
                order_items.append({
                    "order_id": order_id,
                    "product_id": product["product_id"],
                    "quantity": quantity,
                    "unit_price_jpy": product["unit_price_jpy"],
                    "line_total_jpy": quantity * product["unit_price_jpy"],
                })
    return orders, order_items


TABLES = {
    "departments": "dept_id:STRING,name:STRING,head_employee_id:STRING",
    "employees": "employee_id:STRING,name:STRING,department:STRING,title:STRING,hired_date:DATE,location:STRING",
    "products": "product_id:STRING,name:STRING,category:STRING,unit_price_jpy:INTEGER,launched_date:DATE",
    "customers": "customer_id:STRING,name:STRING,email:STRING,city:STRING,prefecture:STRING,signup_date:DATE",
    "orders": "order_id:STRING,customer_id:STRING,order_date:DATE,status:STRING,channel:STRING",
    "order_items": "order_id:STRING,product_id:STRING,quantity:INTEGER,unit_price_jpy:INTEGER,line_total_jpy:INTEGER",
}


def load(table: str, rows: list[dict], workdir: Path) -> None:
    path = workdir / f"{table}.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    cmd = ["bq"]
    if PROJECT:
        cmd += ["--project_id", PROJECT]
    cmd += ["load", "--replace", "--source_format=NEWLINE_DELIMITED_JSON"]
    if table == "orders":
        cmd += ["--time_partitioning_type=DAY", "--time_partitioning_field=order_date"]
    cmd += [f"{DATASET}.{table}", str(path), TABLES[table]]
    subprocess.run(cmd, check=True)
    print(f"{DATASET}.{table}: {len(rows)} rows")


def main() -> None:
    departments, employees = build_departments_and_employees()
    products = build_products()
    customers = build_customers()
    orders, order_items = build_orders(customers, products)
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for table, rows in [("departments", departments), ("employees", employees), ("products", products),
                            ("customers", customers), ("orders", orders), ("order_items", order_items)]:
            load(table, rows, workdir)


if __name__ == "__main__":
    main()
