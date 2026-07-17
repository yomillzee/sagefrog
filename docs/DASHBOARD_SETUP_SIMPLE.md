# Setting Up a New Client Dashboard — Simple Guide

A plain-English, step-by-step guide for standing up a new client dashboard. No
code, no jargon. If you can follow a recipe, you can do this.

> Want the deep technical version instead? See
> [`CREATING_A_NEW_DASHBOARD.md`](CREATING_A_NEW_DASHBOARD.md).

**The whole thing, in four steps:**

1. Create a Google Cloud (BigQuery) project — this is where the client's data lives.
2. Turn on billing for that project.
3. Create the dashboard in the portal.
4. Connect the data sources (connectors).

That's it. Steps 1–2 happen in Google Cloud. Steps 3–4 happen in our portal.

---

## Before you start

You'll need:

- An **admin login** to the portal.
- Access to **Google Cloud Console** (https://console.cloud.google.com) with
  permission to create projects and set up billing.
- The client's marketing accounts (Google Ads, GA4, etc.) already visible to
  our agency logins. (This is usually already true — the agency logs into each
  platform once, and every client reuses it.)

---

## Step 1 — Create the BigQuery (Google Cloud) project

"BigQuery" is Google's data warehouse. Each client gets their own project so
their data stays separate.

1. Go to https://console.cloud.google.com.
2. Top bar → click the **project dropdown** → **New Project**.
3. Give it a clear name (e.g. `acme-marketing`) and click **Create**.
4. **Write down the Project ID.** It's shown right under the name and looks like
   `acme-marketing-472901`. You'll paste this into the portal later.
   ⚠️ The Project **ID** is not the same as the Project **Name** — copy the ID.

### Give our system permission to use it

Our platform reads and writes to the project using one shared "service account."
You need to grant it access:

1. In Google Cloud Console, make sure your new project is selected (top bar).
2. Go to **IAM & Admin → IAM**.
3. Click **Grant access** (or **+ Add**).
4. In "New principals," paste:
   `marketing-data-reader@sagefrog.iam.gserviceaccount.com`
5. Add **two** roles (both are required):
   - **BigQuery Data Editor**
   - **BigQuery Job User**
6. Click **Save**.

That's the only permission step. If you skip a role, connector setup will fail
later with a message telling you exactly which role is missing.

---

## Step 2 — Turn on billing

BigQuery needs a billing account attached, even though normal usage is very cheap
(often a few dollars a month or less).

1. In Google Cloud Console → **Billing**.
2. If the project isn't linked to a billing account, click **Link a billing
   account** and pick (or create) one.
3. Done. You don't need to set budgets or do anything else here.

> Storage cost grows with how much history you pull in — see the backfill note
> in Step 4.

---

## Step 3 — Create the dashboard in the portal

1. Log into the portal and go to **`/admin`**.
2. Find **Dashboards → Add**.
3. Enter:
   - **Slug** — a short, lowercase, no-spaces name used in the web address
     (e.g. `acme`). This becomes `/dashboard/acme`.
   - **Display label** — the pretty name shown on screen (e.g. `Acme Co`).
4. Submit.

The dashboard now exists at `/dashboard/{slug}`. It'll look empty ("no data yet")
until you connect data sources — that's next. There's **no separate step to
register the Google Cloud project**; you enter the Project ID during connector
setup.

---

## Step 4 — Connect the data sources (connectors)

This is where you tell each platform (Google Ads, GA4, LinkedIn, etc.) to start
feeding data into the client's BigQuery project.

1. Open the dashboard → **Connectors** in the sidebar
   (`/dashboard/{slug}/connectors`).
2. You'll see a list of every connector type with a **Connect** button.
3. For each one the client uses, click **Connect** and follow the short wizard:
   1. **Connect** — usually one click (it reuses the agency login).
   2. **Select account** — pick the client's account from the list.
   3. **Destination** — **paste the GCP Project ID from Step 1.**
      👉 **The Project ID is the only thing you enter here.** Leave the dataset
      names exactly as they are — do **not** change them. This is only asked the
      first time.
   4. **Backfill** — choose how much history to pull. See the guidance below.
   5. **Test** — confirms the connection works.
   6. **Finish** — ⚠️ **you must click Finish.** This is what schedules the
      connector to sync every day. If you leave the wizard half-done, it won't
      sync on its own.
4. Repeat for each platform the client uses.

### Choosing the backfill date range

"Backfill" = how many days of past data to load on the first sync. Pick this
per connector based on what the platform allows and what you actually need:

- **More backfill = more BigQuery storage = slightly higher cost.** Only pull
  what's useful.
- **PageSpeed** can only give the **last 5 days** — that's a platform limit,
  so don't expect more.
- **Google Search Console (GSC)** is **slow to backfill**, so only choose the
  range you truly need. Don't pull years of history "just in case."
- For most other connectors, a sensible default (e.g. 90 days to a year) is
  fine. When in doubt, start smaller — you can always pull more later.

---

## Step 5 — See the data

Data doesn't appear until a sync runs. You can either:

- Click **Sync now** on a connector card to pull it immediately, or
- Wait for the **automatic daily sync** (runs overnight).

The first sync can take a little while, especially for slow connectors like GSC.
Once it finishes, the dashboard panels fill in.

### Quick check that it worked

- [ ] The dashboard page loads (not an error).
- [ ] Each connected connector shows a recent, successful sync.
- [ ] The Overview cards and charts show numbers (this means a paid connector
      like Google Ads / LinkedIn / Meta synced).
- [ ] The Website Analytics panels show traffic (this means GA4 synced).

If a connector synced successfully but its panel is empty, it usually just means
there's no data in the date range you picked yet — try a wider backfill or wait
for more days of data.

---

## Quick Q&A

**Do I have to set up the datasets or tables in BigQuery myself?**
No. The system creates everything (datasets, tables, reports) automatically the
first time a connector syncs. You only ever paste the Project ID.

**Do I re-enter the Project ID for every connector?**
You confirm the destination for each connector, but you're just pasting the same
Project ID each time. No other edits.

**Do I need to log into Google Ads / LinkedIn / Meta for each client?**
No. The agency logs into each platform once, and every client reuses that login.
(HubSpot is the one exception — it's connected per client via a link.)

**What if I forgot to click "Finish" on a connector?**
It won't sync automatically. Go back into the connector and run the wizard all
the way to Finish, or use "Sync now" as a one-off. Always finish the wizard.

**Why did connector setup fail with a permissions error?**
Almost always because the service account is missing a role in Google Cloud. Go
back to Step 1 and make sure
`marketing-data-reader@sagefrog.iam.gserviceaccount.com` has **both** BigQuery
**Data Editor** and **Job User** on the project.

**How much does BigQuery cost?**
For a single client's marketing data it's typically very small — usually a few
dollars a month or less. The main cost driver is how much history you backfill,
which is why we recommend pulling only what you need.

**Why is my PageSpeed data only showing a few days?**
That's a PageSpeed limitation — it only provides roughly the last 5 days. Nothing
is broken.

**GSC is taking forever — is something wrong?**
No, Google Search Console is just slow to backfill. Be patient, and next time
only select the date range you actually need.

**The dashboard is empty right after I finished setup — is it broken?**
No. Data shows up only after the first sync runs. Click **Sync now** on a
connector, or wait for the overnight sync.

**Can I add more history later?**
Yes. You can pull a larger backfill later if you find you need more history —
just remember it adds to storage.

---

## The one thing that has to be done by a human

Everything in the portal is automatic once the connectors are set up. The **only**
part that can't be automated is **Steps 1–2** — creating the Google Cloud project,
turning on billing, and granting the two BigQuery roles. That requires Google
Cloud admin access and has to be done by a person, once per client. After that,
the portal handles the rest.
