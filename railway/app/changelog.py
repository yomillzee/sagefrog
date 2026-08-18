"""What's New — the release notes the team reads in the admin panel.

One list, newest first, rendered at ``/admin/changelog``. It exists because the
portal ships continuously and nobody watches the deploy log: when a page moves,
a control changes shape, or a number starts being computed differently, the
people running client calls need somewhere to find out *before* a client asks
them about it.

Rules that keep this useful rather than a second commit log:

* **Only user-visible change.** If someone using the portal could notice it —
  a new page, a control that behaves differently, a metric that now means
  something else, a fix for something they reported — it belongs here. Refactors,
  dependency bumps, and internal plumbing do not.
* **Write it for the person using the page, not the person who wrote the diff.**
  Name the screen, say what changed and why it is better. No file paths, no
  function names, no PR numbers.
* **Newest first, and never rewrite history.** Ship a correction as a new entry;
  editing a shipped one means someone who already read it never learns.
* **One entry per shipped change**, not one per commit. A feature that took six
  commits is one entry, added when it goes to ``main``.

Adding an entry is a one-item edit to :data:`ENTRIES` — no migration, no
template. ``KINDS`` is the closed set of badges; add to it only if a change
genuinely is not one of new / improved / fixed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Badge key → (display label, palette class used by the renderer). Closed set, so
# the page never has to style an unknown badge.
KINDS: dict[str, tuple[str, str]] = {
    "new": ("New", "new"),
    "improved": ("Improved", "improved"),
    "fixed": ("Fixed", "fixed"),
}

DEFAULT_KIND = "improved"


@dataclass(frozen=True)
class Entry:
    """One shipped, user-visible change.

    ``date`` is the ISO day it reached production. ``area`` is the part of the
    portal it lands in, phrased the way the nav does ("Benchmarks", "Accounts",
    "Client dashboards") so a reader can go look at it. ``summary`` is the
    one-sentence version — what changed and why it is better; ``details`` are the
    specifics worth calling out, one short line each, and may be omitted.
    """

    date: str
    title: str
    area: str
    summary: str
    kind: str = DEFAULT_KIND
    details: tuple[str, ...] = field(default_factory=tuple)


# Newest first. Add to the top; never edit or reorder what has shipped.
ENTRIES: tuple[Entry, ...] = (
    Entry(
        date="2026-08-18",
        title="A keyword watchlist to benchmark the keywords we write for",
        area="Search Console",
        kind="new",
        summary=(
            "Search Console has a new Keyword watchlist where each row is one "
            "keyword you entered — with the page it was written for, a 13-week "
            "rank spark line, and its impressions, clicks and CTR — so a "
            "\"this blog is for that keyword\" commitment has somewhere to be "
            "watched."
        ),
        details=(
            "Admins add rows from Edit on the panel: the keyword, plus "
            "optionally the URL or path it is aimed at.",
            "The spark line rises when the rank improves and is green when the "
            "keyword ends the 13 weeks better than it started.",
            "End a keyword with * to count its variants too, instead of only "
            "the exact search.",
            "A keyword that has not earned an impression yet still shows as a "
            "row, so it stays on the list rather than disappearing.",
            "The page's figures are that page's own totals across every search "
            "it appears for — Search Console does not report clicks per "
            "keyword-and-page pair.",
        ),
    ),
    Entry(
        date="2026-08-17",
        title="Search Console follows the date range again",
        area="Search Console",
        kind="fixed",
        summary=(
            "Changing the date range or the comparison period now reloads the "
            "Search Console tab, which used to keep showing the window it was "
            "first opened with."
        ),
        details=(
            "The clicks, impressions and position figures, the query and page "
            "tables, and the branded/target keyword lists all refresh with the "
            "range.",
            "SEMrush figures on the same tab are unchanged — they are a "
            "current snapshot and never depended on the range.",
        ),
    ),
    Entry(
        date="2026-08-17",
        title="Search Console CTR now says whether it is good for that rank",
        area="Search Console",
        kind="improved",
        summary=(
            "Every CTR in the Search Console tables carries a small dot showing "
            "whether the click-through rate is ahead of, in line with, or behind "
            "what a query at that average position normally earns."
        ),
        details=(
            "Green means ahead of the typical range for that position, grey means "
            "in line, amber means behind â a 4% CTR is strong at position 8 and "
            "weak at position 1, and the dot makes that difference readable at a "
            "glance.",
            "Hovering a CTR spells out the comparison, e.g. âBehind the 6â12% a "
            "query at position 3.2 typically earnsâ.",
            "Applies to top queries, top pages, and the branded and target "
            "keyword tables.",
        ),
    ),
    Entry(
        date="2026-08-17",
        title="Timeline moved to Insights",
        area="Client dashboards Â· Insights",
        kind="improved",
        summary=(
            "The Timeline manager â adding the launches, site changes and "
            "budget shifts that get drawn onto the trend charts â moved from "
            "the bottom of the Overview tab to the Insights page in Settings."
        ),
        details=(
            "The dated markers still show up on every trend chart exactly as "
            "before; only the tool for adding and editing them moved.",
        ),
    ),
    Entry(
        date="2026-08-17",
        title="Feature requests post to Slack again",
        area="Feature requests",
        kind="new",
        summary=(
            "New feature requests raised from a client dashboard show up in the "
            "Slack channel again, so nobody has to remember to check the admin "
            "inbox to see one come in."
        ),
        details=(
            "The message says who asked, which client and page they were on, "
            "and links straight to the request in the admin inbox.",
            "The inbox and its unread badge work exactly as before â Slack is "
            "an extra heads-up, not a replacement.",
        ),
    ),
    Entry(
        date="2026-08-17",
        title="Scrollbars look like part of the portal now",
        area="Everywhere",
        kind="improved",
        summary=(
            "When the left menu or a wide table needed to scroll, you got the "
            "browser's default grey scrollbar bolted on top of the design. "
            "It's now a slim rounded bar in the portal's own colours."
        ),
        details=(
            "In the navy menu it's a faint light bar; on white cards and tables "
            "it's a soft grey one.",
            "It stays quiet until you move the pointer over that area, then "
            "darkens so it's easy to grab.",
        ),
    ),
    Entry(
        date="2026-08-17",
        title="Event Tracking has the same sidebar as every other page",
        area="Client dashboards · Event Tracking",
        kind="fixed",
        summary=(
            "Event Tracking was still drawing a sidebar of its own, so opening it "
            "changed the menu: sections that exist for the client went missing, "
            "ones they don't have appeared, and Files and Settings dropped out. "
            "It now uses the same menu as the rest of the client's pages."
        ),
        details=(
            "AI Traffic, Email Performance, LinkedIn Organic and Consent Health "
            "are reachable from Event Tracking now, and Search Console no longer "
            "shows for clients without it.",
            "Sections an admin hid under Settings → Advanced stay hidden here too.",
        ),
    ),
    Entry(
        date="2026-08-17",
        title="Removed the \"Data through\" chip",
        area="Client dashboards",
        kind="fixed",
        summary=(
            "The admin-preview chip showing how current the numbers are has been "
            "taken back off the filter bar."
        ),
    ),
    Entry(
        date="2026-08-16",
        title="Google Ads now shows who the money is going to",
        area="Client dashboards · Campaign explorer",
        kind="new",
        summary=(
            "A new Google Ads demographics panel breaks spend and conversions "
            "down by age and gender, and calls out segments that are taking "
            "budget without returning anything — the \"consider excluding "
            "under-25s\" conversation, with the numbers already lined up."
        ),
        details=(
            "Two tabs, Age and Gender. Each row pairs the segment's share of "
            "spend with its share of conversions, so a segment costing more "
            "than it returns reads as a top-heavy pair of bars.",
            "A segment is only flagged once it has spent past twice what the "
            "account normally pays for a conversion — a quiet week never turns "
            "into a recommendation.",
            "Segments you have already excluded are marked as such and never "
            "recommended again.",
            "The panel reports how much spend Google could not attribute to a "
            "segment, because on Search that is often most of it. Performance "
            "Max reports no demographics at all, so these totals are a subset "
            "of account spend and will not match the campaign figures above.",
            "Appears once a client's next Google Ads sync has run; clients with "
            "no demographic data don't get an empty panel.",
        ),
    ),
    Entry(
        date="2026-08-15",
        title="Insights opens with what's actually worth talking about",
        area="Client dashboards · Insights",
        kind="new",
        summary=(
            "A new \"What's notable\" panel at the top of the Insights page "
            "ranks the movements, missed targets and peer gaps for a client and "
            "explains them in a sentence each — the first place in the portal "
            "that says which numbers deserve attention rather than listing all "
            "of them."
        ),
        details=(
            "Each line names the change and, where one platform is responsible "
            "for most of it, says which: \"Conversions up 38% — Google Ads "
            "accounts for 61% of the change.\"",
            "A change that lines up with something on the Timeline says so, so "
            "the cause travels with the number.",
            "Nothing is estimated and nothing is invented — every line is built "
            "from the same figures as the dashboard, and small numbers are left "
            "out rather than reported as huge percentages.",
            "A source that stopped syncing is called out above everything else, "
            "because it changes how the rest of the list should be read.",
            "Pick Last 7, 30 or 90 days. Set metric goals and switch on peer "
            "benchmarks further down the page to have those checked here too.",
            "Admin-only for now while the wording settles.",
        ),
    ),
    Entry(
        date="2026-08-15",
        title="Peer benchmarks are now off until you switch them on per client",
        area="Client dashboards · Insights settings",
        kind="improved",
        summary=(
            "The industry comparison on the Overview cards no longer appears on "
            "its own. It is off for every client, with a new switch in Insights "
            "settings to turn it on once you have checked that account's "
            "industry tags are right."
        ),
        details=(
            "A benchmark is a claim about how an account is doing against "
            "others, so it should be a decision rather than a default — the "
            "peer set is only as good as the tags behind it.",
            "With the switch off the cards look exactly as they did before, and "
            "the dashboard does no benchmark work at all.",
            "Still an admin-only preview either way: clients do not see the "
            "comparison yet, however the switch is set.",
        ),
    ),
    Entry(
        date="2026-08-15",
        title="Mark what happened on a date, and see it on every trend chart",
        area="Client dashboards · Timeline",
        kind="new",
        summary=(
            "A new Timeline panel on Overview lets you record dated events — a "
            "site migration, a campaign launch, a budget cut — and they appear "
            "as markers on the trend charts, so a dip has its cause sitting "
            "next to it instead of being reconstructed from memory on a call."
        ),
        details=(
            "Add an event with a date (or a date range), a title, a category, "
            "and optional detail. Hover a marker on any trend chart to read it.",
            "New events are internal to the agency until you switch them to "
            "Shared — a client only ever sees the ones you shared, and only the "
            "agency can add, edit or delete them.",
            "Events with a date range mark every chart their range touches, so a "
            "campaign that started last month still shows on this month's view.",
        ),
    ),
    Entry(
        date="2026-08-15",
        title="Set a target per metric and see whether the number is hitting it",
        area="Client dashboards · Insights settings",
        kind="new",
        summary=(
            "Insights settings now takes a goal for Spend, Impressions, Clicks, "
            "Conversions, CTR, CPC and CPA, and the matching Overview card shows "
            "progress against it — the first thing on the dashboard that says "
            "whether a number is where it should be, not just which way it moved."
        ),
        details=(
            "Spend, impressions, clicks and conversions are entered as monthly "
            "totals and scaled to whichever date range is on screen, so one goal "
            "works for a week, a month to date, or a custom range.",
            "CTR, CPC and CPA are entered as the rate itself and compared "
            "directly. CPC and CPA are read as ceilings — under target is good.",
            "Spend is graded as a band: well under plan reads as a miss too, not "
            "as a win.",
            "While this is in preview only admins see the result on the "
            "dashboard, so you can set targets before clients ever see them.",
        ),
    ),
    Entry(
        date="2026-08-15",
        title="See how an account compares with others in its industry",
        area="Client dashboards",
        kind="new",
        summary=(
            "Overview metric cards can now carry a peer line — \"ahead of health "
            "care · 1.6% median (n=9)\" — answering the question clients actually "
            "ask on a call: is this good for a company like us?"
        ),
        details=(
            "Peers come from the industry tags on the Accounts page, falling back "
            "to all clients when an account is the only one in its industry.",
            "The account is left out of its own peer group, so the comparison is "
            "with everyone else rather than partly with itself.",
            "A peer group of three or fewer is labelled \"thin\" — directional, "
            "not a benchmark.",
            "Admin-only preview for now.",
        ),
    ),
    Entry(
        date="2026-08-15",
        title="A \"Data through\" chip says how current the numbers are",
        area="Client dashboards",
        kind="new",
        summary=(
            "The filter bar now shows the date the data actually runs to, so a "
            "soft-looking week can be read correctly when one platform stopped "
            "syncing partway through it."
        ),
        details=(
            "The chip reports the oldest connector's latest day, because a "
            "combined figure is only as current as its slowest source; hover it "
            "for a per-source breakdown.",
            "It turns amber, then red, as that lag grows. Platforms normally "
            "report one day behind, which counts as current.",
            "Admin-only preview for now.",
        ),
    ),
    Entry(
        date="2026-08-15",
        title="A metric card's top bar turns green or red with its change",
        area="Client dashboards",
        kind="improved",
        summary=(
            "The colour bar across the top of each metric card now matches that "
            "card's change against the comparison period, so a screen of cards "
            "reads as good or bad at a glance before you read a single number."
        ),
        details=(
            "It follows what the change means, not which way the arrow points: a "
            "falling CPA or CPC is green, a rising one is red.",
            "Cards with nothing to compare against, a flat change, or no better "
            "direction — Spend, for one — keep the usual blue bar.",
        ),
    ),
    Entry(
        date="2026-08-15",
        title="The dashboard filters fit on one line on a phone",
        area="Client dashboards",
        kind="improved",
        summary=(
            "The Range and Compare pickers dropped their captions for a calendar "
            "icon and a \"vs\", which puts both on a single row on a phone instead "
            "of stacking, and the platform chips no longer run off the card."
        ),
        details=(
            "Range reads \"Last 30 days\" behind a calendar icon; Compare reads "
            "\"vs Previous period\".",
            "The Platform row lost its caption too — the chips already say what "
            "they do — and now wraps onto a second line rather than pushing "
            "Microsoft off the edge of the screen.",
            "Long money values no longer spill outside their card on a narrow "
            "phone.",
        ),
    ),
    Entry(
        date="2026-08-14",
        title="The campaign explorer table now ends in a totals row",
        area="Campaign explorer",
        kind="new",
        summary=(
            "A Total row sits at the bottom of the campaign table, adding up spend, "
            "impressions, clicks, CTR and conversions for whatever is on screen — so "
            "the account-level number is right there instead of being added up by hand."
        ),
        details=(
            "It totals what you are looking at: narrow to one platform, pick a filter, "
            "or change the date range and the row re-totals to match.",
            "CTR is the blended rate — total clicks over total impressions — not an "
            "average of the campaigns' CTRs, so a tiny campaign cannot skew it.",
            "The row also shows how many campaigns it is covering.",
            "Verified conversions total the same way, and stay as a dash when none of "
            "the campaigns in view are matched to GA4.",
        ),
    ),
    Entry(
        date="2026-08-14",
        title="See which companies and job titles LinkedIn ads reached",
        area="Campaign explorer",
        kind="new",
        summary=(
            "A LinkedIn audience panel on the Campaign explorer shows who actually "
            "saw and clicked the ads — by company, job title, job function, "
            "seniority, industry and company size."
        ),
        details=(
            "Tabs across the top switch between the six breakdowns; each lists the "
            "top categories with impressions, share of reach, clicks, CTR and spend.",
            "LinkedIn only reports these figures over a fixed window, so the panel "
            "shows a badge naming the window it is displaying (last 30 or last 90 "
            "days) rather than following the date range at the top of the page.",
            "The numbers are approximate by design and will not add up to campaign "
            "totals: LinkedIn withholds categories with very few events to protect "
            "member privacy.",
            "The panel only appears for clients with LinkedIn Ads connected, and can "
            "be reordered or hidden like any other explorer panel.",
        ),
    ),
    Entry(
        date="2026-08-14",
        title="Search the connector list, and LinkedIn Organic has its logo",
        area="Settings · Connectors",
        kind="improved",
        summary=(
            "A search box above the connector cards narrows the list as you type, "
            "and LinkedIn Organic is no longer the one card sitting next to an "
            "empty square."
        ),
        details=(
            "Typing filters on the connector's name, so \"linkedi\" leaves just "
            "LinkedIn Ads and LinkedIn Organic on screen.",
            "Short names work too — \"gsc\" finds Search Console, \"ga4\" finds "
            "Google Analytics 4.",
            "Clearing the box brings every connector back.",
            "LinkedIn Organic now shows an outlined LinkedIn mark, so it reads "
            "apart from LinkedIn Ads at a glance.",
        ),
    ),
    Entry(
        date="2026-08-14",
        title="Campaign Explorer panels rearrange like the Overview ones",
        area="Client dashboards · Campaign Explorer",
        kind="new",
        summary=(
            "Campaign Explorer now has the same Edit layout mode as the Overview "
            "home, so you can hide a panel a client doesn't need or drag the "
            "panels into the order that suits them."
        ),
        details=(
            "Hover the Campaign Explorer item in the sidebar, click the ⋮, "
            "then Edit layout — the same way you already edit Overview.",
            "Campaign explorer, Keyword Performance and Budget tracking each get "
            "a Hide / Show button and a drag handle while you're editing.",
            "A hidden panel is greyed out for you and simply isn't there for the "
            "client; changes save on their own and Done leaves edit mode.",
            "The budget tracker's old on/off entry in that ⋮ menu is gone — it is "
            "now the Budget tracking panel's Hide / Show, and it still matches "
            "the Show on Explorer switch on the settings page.",
        ),
    ),
    Entry(
        date="2026-08-14",
        title="Website Analytics tables run the full width, with a tab for the pair",
        area="Client dashboards · Website Analytics",
        kind="improved",
        summary=(
            "Pages and Landing Pages no longer sit squeezed side by side — each "
            "pair of panels now shares one full-width card you switch with a tab, "
            "so a page path is readable instead of cut off after a few characters."
        ),
        details=(
            "Pages is the tab you land on; Landing Pages is one click away.",
            "Traffic acquisition and New user acquisition pair up the same way, "
            "with Traffic acquisition open by default.",
            "With the extra room, page paths show far more of the URL before "
            "they truncate; the full path is still in the hover tooltip.",
            "Sorting, the path filter, the events selector and drag-to-resize "
            "columns all work exactly as before on whichever tab is open.",
        ),
    ),
    Entry(
        date="2026-08-14",
        title="A Sagefrog copyright line closes out every page",
        area="Client dashboards · Admin",
        kind="improved",
        summary=(
            "Every page in the portal now ends with a quiet Sagefrog copyright "
            "line, so a dashboard shared with a client reads as ours all the way "
            "to the bottom."
        ),
        details=(
            "Small, grey, below the content — it stays out of the way of the page.",
            "On a short page it settles at the bottom of the window rather than "
            "floating under the last card.",
            "The year keeps itself current.",
        ),
    ),
    Entry(
        date="2026-08-13",
        title="An account can sit in more than one industry",
        area="Benchmarks · Accounts",
        kind="new",
        summary=(
            "Industry is now a multi-select, so an account that straddles two "
            "markets is benchmarked against both books instead of being forced "
            "into whichever one you picked first."
        ),
        details=(
            "Accounts → ⋮ → Industry… is a checklist now: tick every bucket that fits.",
            "A multi-tagged account shows a chip per industry on its card, and the "
            "account filter finds it under any of them.",
            "On Benchmarks it appears in each of its industry rows, labelled "
            "“also in …” — and still counts once in the All clients baseline.",
            "Nothing to redo: accounts tagged before this shipped keep the tag they had.",
        ),
    ),
    Entry(
        date="2026-08-13",
        title="What's New lives in the admin panel",
        area="Admin",
        kind="new",
        summary=(
            "This page. Significant changes to how the portal looks or works get "
            "written up here when they ship, so nobody finds out from a client."
        ),
    ),
)


def entries() -> tuple[Entry, ...]:
    """Every entry, newest first.

    Sorted here rather than trusted from the literal, so an entry appended in the
    wrong place still reads correctly on the page.
    """
    return tuple(sorted(ENTRIES, key=lambda e: e.date, reverse=True))


def kind_label(kind: str) -> str:
    return KINDS.get((kind or "").strip().lower(), KINDS[DEFAULT_KIND])[0]


def kind_class(kind: str) -> str:
    """CSS modifier for a badge — unknown kinds fall back rather than going unstyled."""
    return KINDS.get((kind or "").strip().lower(), KINDS[DEFAULT_KIND])[1]


def latest_date() -> str | None:
    """ISO date of the most recent entry, or None when the log is empty."""
    all_entries = entries()
    return all_entries[0].date if all_entries else None
