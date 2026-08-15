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
