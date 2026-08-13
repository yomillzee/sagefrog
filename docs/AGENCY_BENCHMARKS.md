# Agency Benchmarks (industry averages)

Every account carries one **industry tag**. The admin **Benchmarks** page
(`/admin/benchmarks`) uses those tags to answer the question that comes up on
every QBR call — *"is a 2.1% search CTR good for a company like us?"* — by
showing the distribution of each metric inside an industry bucket, next to the
agency-wide baseline.

## Tagging an account

Admin → **Accounts** → the ⋮ menu on any card → **Industry…** → pick a bucket →
Save. The tag shows as a chip on the account card, and the "Filter accounts…"
box searches it, so typing `manufacturing` shows that book.

The tag is descriptive metadata only. It changes nothing on the client's own
dashboard; its sole consumer is the Benchmarks rollup.

Accounts with no tag read as **Unassigned**. They still count toward the "All
clients" row, but they are in no industry bucket — so the page reports how many
are still untagged, and links back to Accounts to finish the job.

## The buckets

Defined in `railway/app/client_industries.py`, which is the only source of
truth — the admin dropdown, the chips, and the rollup all read it.

| Bucket | Bucket |
| --- | --- |
| Health & Life Sciences | Consumer Products & Retail |
| Industrial Manufacturing | Real Estate & Hospitality |
| Technology & Software | Education & Nonprofit |
| Financial Institutions & Insurance | Government & Public Sector |
| Business & Professional Services | Media & Communications |
| Architecture, Engineering & Construction | Agriculture & Food |
| Energy & Utilities | Other |
| Transportation & Logistics | |
| Chemicals & Materials | |

**Adding a bucket** is a one-line edit to `INDUSTRIES` in that module — no
migration, no renderer change. Two rules:

- **Never rename a key**, only its label. The key is what sits in Postgres
  (`dashboard_clients.industry`); renaming one orphans every row using it. (An
  orphaned key degrades to "Unassigned" rather than erroring, but the tag is
  lost.)
- **Don't add a bucket that will hold one client forever.** Small buckets make
  useless benchmarks — that's what `Other` is for.

## What gets measured

| Metric | Source | Notes |
| --- | --- | --- |
| Website sessions | GA4 mart (`vw_ga4_traffic_acq_daily`) | All traffic, not just paid |
| Paid CTR | Paid-media mart (`vw_paid_media_daily`) | Clicks ÷ impressions |
| Paid CPC | " | Spend ÷ clicks |
| Paid conversion rate | " | Conversions ÷ clicks, as each platform counts them |
| Paid cost per conversion | " | Spend ÷ conversions |
| Paid spend | " | A size marker, not a score |
| Paid impressions | " | " |
| LinkedIn followers | `raw_linkedin_organic.follower_daily` | Latest lifetime total; not window-scoped |

Paid metrics respect the **Platform** filter (All paid media / Google Ads /
LinkedIn Ads / …), which only offers channels some client actually ran on.
**Window** is *this month* or *last 30 days*.

Adding a metric means one entry in the `METRICS` registry in
`dashboard/services/agency_benchmarks_service.py` — a key, a label, a display
format, and a function that turns one client's totals into that client's value.
The page builds its dropdown from the registry, so nothing else changes.

## How to read the numbers

Three choices are deliberate, because a benchmark that lies is worse than no
benchmark:

- **The median is the headline, not the mean.** One enterprise spender drags a
  mean far from what a typical account experiences. The mean and the min–max
  range sit alongside it, so a skewed bucket is visible.
- **Ratios are per client, then averaged.** Each client's own CTR is computed
  first, and the median is taken across clients — so the benchmark describes a
  *client*, not the agency's blended book, which the biggest account would
  otherwise dominate.
- **A client with no denominator is excluded, not counted as zero.** No paid
  impressions in the window means "no CTR", not "0% CTR". Every row shows its
  own `n`, and a bucket under three contributing accounts is flagged amber:
  directional, not a benchmark.

Expanding an industry row lists its accounts with each one's value and its gap
to that industry's median — the number worth acting on.

## Cost

Effectively no extra BigQuery. The per-client paid-media and GA4 reads go
through the same cache keys and the same fetch windows the **Health** page
already warms (`agency_trends_service.overview_fetch_bounds`), so a benchmark
refresh normally reads Postgres cache only. The one new read is the LinkedIn
follower total — one row per client, cached for the day. The whole payload is
cached for 15 minutes per (day, window, platform).

That shared-window contract is load-bearing: a longer window (90 days) would
mean a wider, uncached read for every client, which is why the page offers only
windows that fit inside what Health already fetches.
