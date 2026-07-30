---
name: company
description: Who Soramame Inc. (ソラマメ株式会社) is and how its business data is laid out in the soramame BigQuery dataset. Consult this whenever a task involves the company's sales, orders, products, customers, employees, or departments — including indirect asks like "how is revenue trending", "which channel is growing", or "who runs logistics".
---

# Soramame Inc. (ソラマメ株式会社)

All company data referenced here is fictional sample data for this platform.

## Company profile

- **Name**: ソラマメ株式会社 (Soramame Inc.)
- **Vision**: 「毎日の暮らしを、少しやさしく。」 — make everyday life a little
  kinder, to people and the planet
- **Business**: Tokyo-based D2C e-commerce, founded 2021. Sells sustainable
  household and lifestyle goods (kitchen, bath, stationery, textiles) through
  its own web store, a mobile app, external marketplaces, and a small
  wholesale line
- **Scale**: ~48 employees in 6 departments, ~500 registered customers,
  revenue on a growth trajectory toward ¥1.2B/year

## Organization

Six departments, each headed by a 部長 (head): 経営企画 (Corporate
Planning), EC事業部 (E-Commerce), 商品企画 (Merchandising), CS (Customer
Success), 物流 (Logistics), コーポレートIT (Corporate IT). Head assignments
live in `departments.head_employee_id`.

## Data dictionary — dataset `soramame`

All money columns are JPY integers. Dates/timestamps are JST business dates.

| table | grain | key columns |
|---|---|---|
| `departments` | one row per department | `dept_id`, `name`, `head_employee_id` |
| `employees` | one row per employee | `employee_id`, `name`, `department`, `title`, `hired_date`, `location` |
| `products` | one row per SKU | `product_id`, `name`, `category`, `unit_price_jpy`, `launched_date` |
| `customers` | one row per registered customer | `customer_id`, `name`, `email`, `city`, `prefecture`, `signup_date` |
| `orders` | one row per order | `order_id`, `customer_id`, `order_date` (partition column — always filter on it), `status`, `channel` |
| `order_items` | one row per line item | `order_id`, `product_id`, `quantity`, `unit_price_jpy`, `line_total_jpy` |

## How to join

- Revenue = `orders` ⋈ `order_items` on `order_id`, sum `line_total_jpy`.
  Exclude `status IN ('cancelled', 'refunded')` unless asked otherwise.
- Customer behavior = `orders` ⋈ `customers` on `customer_id`.
- Product performance = `order_items` ⋈ `products` on `product_id`.
- `orders.channel` is one of `web`, `app`, `marketplace`, `wholesale`.
- `orders.status` is one of `delivered`, `shipped`, `processing`,
  `cancelled`, `refunded`.

`orders` is day-partitioned on `order_date`; bound every query on it
(e.g. `WHERE order_date >= DATE_SUB(CURRENT_DATE('Asia/Tokyo'), INTERVAL 90 DAY)`).
