---
name: company
description: Who Lumen Works, Inc. is and how its business data is laid out in the lumen BigQuery dataset. Consult this whenever a task involves the company's revenue, subscriptions, churn, accounts, product usage, support tickets, employees, or departments — including indirect asks like "how is MRR trending", "which accounts are at risk", or "who runs Customer Success".
---

# Lumen Works, Inc.

All company data referenced here is fictional sample data for this platform.

## Company profile

- **Name**: Lumen Works, Inc.
- **Vision**: "Bring clarity to every team's work."
- **Business**: Tokyo-based B2B SaaS company, founded 2021. Its product
  **Lumen** is a project-management and collaboration platform sold on
  seat-based monthly subscriptions (Starter / Pro / Enterprise), priced
  in JPY for the Japanese market
- **Scale**: ~48 employees in 6 departments, ~400 customer accounts from
  SMB to enterprise

## Organization

Six departments, each with a Head: Corporate, Engineering, Product,
Sales, Customer Success, Marketing. Head assignments live in
`departments.head_employee_id`.

## Data dictionary — dataset `lumen`

All money columns are JPY integers. Dates are JST business dates;
timestamps carry a JST offset.

| table | grain | key columns |
|---|---|---|
| `departments` | one row per department | `dept_id`, `name`, `head_employee_id` |
| `employees` | one row per employee | `employee_id`, `name`, `department`, `title`, `hired_date`, `location` |
| `plans` | one row per pricing plan | `plan_id`, `name`, `monthly_price_jpy_per_seat` |
| `accounts` | one row per customer company | `account_id`, `company_name`, `industry`, `segment` (SMB/Mid/Enterprise), `signup_date` |
| `subscriptions` | one row per subscription period | `subscription_id`, `account_id`, `plan_id`, `seats`, `started_date`, `ended_date` (NULL = active) |
| `invoices` | one row per account-month billed | `invoice_id`, `account_id`, `billing_month` (first of month), `amount_jpy`, `status` (paid/overdue) |
| `support_tickets` | one row per ticket | `ticket_id`, `account_id`, `opened_at`, `resolved_at`, `category`, `priority`, `csat` (1–5, NULL if unrated) |
| `usage_daily` | one row per account per active day | `usage_date` (partition column — always filter on it), `account_id`, `active_users`, `projects_created`, `tasks_created` |

## How to analyze

- **MRR / revenue**: sum `invoices.amount_jpy` by `billing_month` — the
  simplest truth. Live MRR = active `subscriptions` (`ended_date IS
  NULL`) joined to `plans`, `SUM(seats * monthly_price_jpy_per_seat)`.
- **Churn vs upgrade**: an ended subscription immediately followed by a
  new one for the same account is an upgrade; an ended subscription with
  no successor means the account churned.
- **Engagement**: `usage_daily` joined to `subscriptions` on account and
  date range — `active_users / seats` is the adoption rate.
- **Support health**: ticket volume by `category`/`priority`, resolution
  time `TIMESTAMP_DIFF(resolved_at, opened_at, HOUR)`, and `csat`.
- `usage_daily` is day-partitioned on `usage_date`; bound every query on
  it (e.g. `WHERE usage_date >= DATE_SUB(CURRENT_DATE('Asia/Tokyo'),
  INTERVAL 90 DAY)`).
