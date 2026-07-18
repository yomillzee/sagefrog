# Creating a New Client Dashboard — Simple Guide

A short, plain-language checklist for standing up a new client dashboard on the
Sagefrog portal. Almost everything is automated — you do two things in GCP, the
rest in the portal.

> Need the full reference (data-flow diagrams, mart ownership, scalability
> notes)? See [`CREATING_A_NEW_DASHBOARD.md`](./CREATING_A_NEW_DASHBOARD.md).

---

## What you need before you start

1. **A GCP project for the client** (create one, or reuse an existing one).
2. On that project, grant the shared service account
   `marketing-data-reader@sagefrog.iam.gserviceaccount.com` **both** roles:
   - **BigQuery Data Editor**
   - **BigQuery Job User**

That's the only manual, GCP-side work. The connector wizard checks these two
roles and fails with a clear message if either is missing.

---

## The 5 steps (in the portal)

### 1. Create the dashboard
Go to **`/admin`** → **Dashboards** → **Add**. Enter a **slug** (e.g. `acme`)
and a **display label** (e.g. `Acme Co`), then submit. The dashboard is live
immediately at `/dashboard/{slug}` with an empty "no data yet" state.

### 2. Connect each platform
Open the dashboard → **Connectors**. For every platform the client uses
(Google Ads, GA4, LinkedIn, Meta, GSC, SEMrush, HubSpot), click **Connect** and
run the wizard **all the way to the end**:

> **Connect → select account → confirm destination → backfill → test → finish**

At **confirm destination** you enter the client's **GCP project ID**; the app
creates the datasets and verifies BigQuery access on the spot.

⚠️ **Always click Finish.** A connector left mid-wizard is not enrolled in the
daily sync, and the cron will skip it.

### 3. Run the first sync
Data only appears after a sync. Trigger one now instead of waiting:
- A connector card's **Sync now** button, or
- The settings page **Refresh — last 30 days** button.

Otherwise the daily cron (**11:30 UTC**) syncs every connected client
automatically.

### 4. Wait for data to land
Each connector writes its raw data and rebuilds its own panels. The **Overview**
Summary + Trend fill once a paid connector (Google Ads / LinkedIn / Meta) has
synced; other panels fill as their connectors sync.

### 5. Verify
Check that these populate:
- [ ] `/dashboard/{slug}` loads the dashboard (not the old snapshot page).
- [ ] Each connected connector card shows a successful last sync.
- [ ] Overview **Summary + Trend** show numbers.
- [ ] Website Analytics panels show data (GA4).
- [ ] Explorer tabs (Google / LinkedIn / Meta) show ads.
- [ ] Search Console / SEMrush panels populate (if used).

If Summary is empty but a paid connector synced fine, check that its raw
`campaign_daily` table has rows for the selected date range.

---

## One-time agency setup (already done, not per client)

- **Agency OAuth** — the agency login for Google Ads, GA4, LinkedIn, and Meta is
  connected once in `/admin` and reused for every client. You do **not**
  re-authorize these per client. (HubSpot is the exception — it's authorized per
  client.)
- **Shared service account** — `marketing-data-reader@sagefrog.iam.gserviceaccount.com`
  is the single identity the app uses for BigQuery on every client project.

---

## The two things that can't be automated

Both are inherent to GCP and must be done by a human, once per client:

1. **Create the GCP project + grant IAM.** The app can't create GCP projects
   (needs org admin + billing). Grant the service account **BigQuery Data
   Editor** and **BigQuery Job User**.
2. **Stop any old Dataform schedule.** Dataform is retired (all marts are
   app-built), but if a client project still has a **scheduled** Dataform
   workflow, disable it in **GCP Console → Dataform** so it can't overwrite the
   app-built marts.
