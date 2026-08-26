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
        date="2026-08-26",
        title="Budget tracking leads with the projection, and hides its settings",
        area="Client dashboards · Campaign explorer",
        kind="improved",
        summary=(
            "The budget card now shows the projected month end and the pacing "
            "line, with the monthly goal and active-day settings tucked behind "
            "a kebab menu in its header."
        ),
        details=(
            "Spent to date, the monthly goal and the suggested daily amount lost their cards — the pacing line under the chart already says all three.",
            "The goal box and the weekday picker (which days count as active) now open from the ⋮ menu next to the range chips, for admins only.",
        ),
    ),
    Entry(
        date="2026-08-26",
        title="Keyword Performance drops its summary banner",
        area="Client dashboards · Campaign explorer",
        kind="improved",
        summary=(
            "The blue line above the keyword table — top-keyword spend share and "
            "keywords spending without a conversion — is gone; the table below it "
            "already shows both, sortable."
        ),
    ),
    Entry(
        date="2026-08-26",
        title="Paid trends can chart several metrics at once",
        area="Client dashboards · Campaign explorer",
        kind="improved",
        summary=(
            "The metric chips above Paid trends are now toggles rather than a "
            "one-of-six pick, so spend, clicks and CTR can sit on the same "
            "timeline instead of being compared by flipping back and forth."
        ),
        details=(
            "Each chip carries the colour of the line it adds, and the legend under the chart totals every selected metric over the window.",
            "Every metric keeps its own scale, so adding CTR to a spend line does not flatten either of them.",
            "The dashed comparison-period line still shows while exactly one metric is picked; with two or more the legend carries the vs-previous change instead.",
        ),
    ),
    Entry(
        date="2026-08-26",
        title="Campaign explorer settings moved into one menu, and GA4-verified conversions can be hidden",
        area="Client dashboards · Campaign explorer",
        kind="improved",
        summary=(
            "The Campaigns and Edit filters buttons now live behind a single "
            "options menu at the right of the Campaign explorer heading, which "
            "also has a new switch for turning the GA4-verified conversion "
            "column and card off."
        ),
        details=(
            "The heading keeps just the Platform filter and the row count, so it stays readable on a laptop.",
            "The GA4-verified switch is available to everyone, not only admins, and remembers itself per browser — it changes what you see, not the client's settings.",
            "With it off, the explorer table, its total row and the summary cards drop the GA4 column entirely.",
        ),
    ),
    Entry(
        date="2026-08-26",
        title="Meta ad thumbnails stop going missing on busy accounts",
        area="Client dashboards · Campaign explorer",
        kind="fixed",
        summary=(
            "New Meta ads sometimes showed up in the explorer with no creative "
            "preview — Meta was cutting the sync off partway through fetching "
            "thumbnails on accounts with a lot of ads. The sync now asks for far "
            "fewer of them, so previews keep up with the ads."
        ),
        details=(
            "Only ads that are actually missing a preview get looked up, instead of every ad the account has ever run.",
            "Spend, clicks and conversions were never affected — those come from a separate request and were always complete.",
        ),
    ),
    Entry(
        date="2026-08-25",
        title="Campaign explorer: Verified conv. (GA4) now covers Microsoft Ads",
        area="Client dashboards · Campaign explorer",
        kind="new",
        summary=(
            "Microsoft/Bing campaigns now get a GA4-verified conversions figure "
            "in the explorer, matched the same way LinkedIn already is."
        ),
        details=(
            "Matched by campaign name (Microsoft has no native GA4 link, like "
            "Google Ads does), so figures can differ from the platform's own "
            "Conv. column when a campaign's GA4 name doesn't line up.",
            "Shown at the campaign row; ad group and ad rows underneath still "
            "show a dash.",
        ),
    ),
    Entry(
        date="2026-08-25",
        title="Date range picker has a This Year option",
        area="Client dashboards · Range picker",
        kind="new",
        summary=(
            "The Range dropdown now offers \"This year\" alongside the other "
            "calendar presets, for a year-to-date view compared against the "
            "same stretch of last year."
        ),
    ),
    Entry(
        date="2026-08-24",
        title="Campaign explorer: see one conversion action at a time",
        area="Client dashboards · Campaign explorer",
        kind="new",
        summary=(
            "The Conv. column now has a selector next to it. Pick a single "
            "conversion action — Contact form, Phone call, a Microsoft goal, "
            "Meta leads — and the whole table, the total row and the "
            "Conversions card narrow to just that action."
        ),
        details=(
            "One list across every platform, ordered by how much each action actually converted.",
            "Google and Meta answer at every level of the tree; Microsoft answers per ad group, so its individual ads show a dash rather than a made-up split.",
            "LinkedIn shows a dash throughout — its reporting can’t say which campaign a conversion belongs to.",
            "A dash always means “this platform doesn’t report that far down”, never zero.",
            "The choice sticks between visits, and the vs-previous arrow steps aside while an action is selected.",
            "Nothing changes until somebody picks an action, and the actions appear after each platform’s next sync.",
        ),
    ),
    Entry(
        date="2026-08-24",
        title="Bluesky has a dashboard page",
        area="Client dashboards · Bluesky",
        kind="new",
        summary=(
            "Clients with Bluesky connected get a Bluesky item in the sidebar: "
            "followers, posts and engagement for the selected date range, a "
            "follower trend, and the posts that earned the most."
        ),
        details=(
            "Every figure compares against the same length of time immediately before the range, the way LinkedIn Organic does.",
            "Top posts link straight to the post on Bluesky, and the table sorts by any column.",
            "The page states plainly that Bluesky has no impressions, reach or clicks, so nobody hunts for columns that can't exist.",
            "Instead of an engagement rate, which would need impressions, it reports engagements per post.",
        ),
    ),
    Entry(
        date="2026-08-24",
        title="Bluesky joins the connector directory",
        area="Connectors · Bluesky",
        kind="new",
        summary=(
            "You can now connect a client's Bluesky account and sync its "
            "followers and post engagement on the same nightly schedule as "
            "every other source — all it needs is the handle."
        ),
        details=(
            "No login or password: paste the handle (or the profile link) and test the connection.",
            "Each sync records followers, following and post count, plus likes, reposts, replies and quotes for every post in the window.",
            "Bluesky publishes no impressions, reach or clicks, so those columns stay empty — the network does not measure them.",
            "Engagement counts keep rising after a post goes out, so each day is stored as its own snapshot and late likes still show up.",
        ),
    ),
    Entry(
        date="2026-08-22",
        title="LinkedIn Ads figures count only the connected ad account",
        area="Client dashboards · LinkedIn Ads",
        kind="fixed",
        summary=(
            "Spend, clicks, impressions, conversions and the campaign table were "
            "totalling every LinkedIn ad account stored for the client, not the one "
            "the dashboard is set up against. Any client running a second ad account "
            "was seeing the two blended into one set of numbers."
        ),
        details=(
            "No figures change for a client with a single ad account, which is every client today.",
            "Creative performance was already scoped correctly and is unaffected.",
            "A client with no ad account configured still sees everything held for them, as before.",
        ),
    ),
    Entry(
        date="2026-08-22",
        title="Every LinkedIn Organic metric now shows period-over-period change",
        area="Client dashboards · LinkedIn Organic",
        kind="improved",
        summary=(
            "Only Followers told you whether the number was going the right way. "
            "Posts, Impressions, Reach, Reactions, Comments, Avg. engagement and "
            "Page views now each carry an up/down badge against the same length "
            "of time immediately before the selected range."
        ),
        details=(
            "Change the date range and the comparison moves with it — last 30 days is measured against the 30 days before that.",
            "Hover a badge to see the previous period's figure.",
            "Avg. engagement is shown in percentage points, so 4.0% \u2192 5.0% reads as +1.00 pts rather than +25%.",
            "A metric with no history that far back shows no badge instead of a meaningless jump from zero.",
        ),
    ),
    Entry(
        date="2026-08-22",
        title="Follower demographics name industries instead of numbering them",
        area="Client dashboards · LinkedIn Organic",
        kind="fixed",
        summary=(
            "The By industry panel read “Industry 11”, “Industry 105” — LinkedIn "
            "ids, not industries. It now names them (“Management Consulting”, "
            "“Professional Training & Coaching”), and any category that still "
            "can't be named is left out rather than shown as an id."
        ),
        details=(
            "Existing dashboards fix themselves on the next page load — no re-sync needed.",
            "By region and the paid Job title / Industry breakdowns were looking names up the wrong way and get the same fix; regions fill in on the next overnight sync.",
        ),
    ),
    Entry(
        date="2026-08-20",
        title="Each Sessions & engagement metric explains itself",
        area="Client dashboards · Website Analytics",
        kind="improved",
        summary=(
            "The paragraph under the heading only ever described the card you "
            "had selected. Hover any of the four cards and it explains that "
            "metric — what it counts, and how the weekly points are built."
        ),
    ),
    Entry(
        date="2026-08-20",
        title="Three more metrics on Website Analytics' session card",
        area="Client dashboards · Website Analytics",
        kind="improved",
        summary=(
            "The Average session duration module now shows Total sessions, "
            "New users, and Engagement rate too — click any of the four cards "
            "to see its trend, and the chart is now a line instead of bars."
        ),
    ),
    Entry(
        date="2026-08-20",
        title="Site Performance explains itself",
        area="Client dashboards · Site Performance",
        kind="improved",
        summary=(
            "Every score and Core Web Vitals box now has a hover explanation in "
            "plain English, and each sparkline draws its goal as a dotted line "
            "so you can see at a glance whether the site is inside it."
        ),
        details=(
            "The “?” on a card says what the metric means to a visitor — no Lighthouse jargon.",
            "The trend line is green when the page is inside the goal, amber or red when it is not.",
            "The separate “goal ≤ …” caption is gone; hover the sparkline for the full Good / Needs improvement / Poor ranges.",
        ),
    ),
    Entry(
        date="2026-08-20",
        title="Switching to a client opens much faster",
        area="Client dashboards",
        kind="fixed",
        summary=(
            "Picking a client from the workspace switcher was slow the first "
            "time you opened them — sometimes several seconds before a single "
            "number appeared. Three separate delays were stacked on that first "
            "load; all three are gone."
        ),
        details=(
            "The Overview's cards no longer queue behind a background data-"
            "freshness check. They start loading the moment the page does.",
            "Every page was re-checking the shape of several database tables "
            "before it could draw the client list in the sidebar. That now "
            "happens once when the app starts.",
            "A client with a pinned default date range (Range → Make default) "
            "was never getting its numbers pre-loaded after a sync, so the "
            "first person in each day waited for every card. It is now pre-"
            "loaded for the range that client actually opens on.",
        ),
    ),
    Entry(
        date="2026-08-19",
        title="A Paid trends chart on the Campaign explorer",
        area="Campaign explorer",
        kind="new",
        summary=(
            "The explorer says which campaign; it could never say when. A new "
            "Paid trends panel plots one metric a day (or a week) across the "
            "selected range, with the comparison window behind it."
        ),
        details=(
            "Chips switch between Spend, Impressions, Clicks, Conversions, CTR "
            "and CPC — one metric at a time, because impressions and clicks on "
            "one axis flattens whichever is smaller.",
            "Daily / Weekly toggle, same as the website trend charts.",
            "It follows the Platform chips, so filtering to LinkedIn re-cuts "
            "the line as well as the table.",
            "Timeline events land on it, and each event now says which charts "
            "it belongs on — see the Timeline entry below.",
        ),
    ),
    Entry(
        date="2026-08-19",
        title="A timeline event can be pinned to ads charts, analytics charts, or both",
        area="Settings · Timeline",
        kind="new",
        summary=(
            "A budget change explains a spend line, not a sessions line. Each "
            "timeline event now carries the charts it belongs on, so its marker "
            "stops turning up where it explains nothing."
        ),
        details=(
            "Two tick boxes in the event editor: Ads trends and Analytics "
            "trends. Both ticked is the default and the common case.",
            "Every event that already exists stays ticked for both, so nothing "
            "moves until you change it.",
            "The event list shows the scope on any event that is not on both.",
        ),
    ),
    Entry(
        date="2026-08-19",
        title="Keyword Performance follows the explorer's filters, and names its window",
        area="Campaign explorer",
        kind="fixed",
        summary=(
            "The keyword table sat under the campaign tree ignoring the "
            "Platform chips, the filter dropdowns and the campaign list it was "
            "filed under. It now shows the same slice, and says which date "
            "range it is showing."
        ),
        details=(
            "Filtering to LinkedIn empties the keyword table rather than "
            "leaving Google keywords contradicting the tree above it.",
            "The insight banner and the match-type chips count the same rows "
            "the table is showing.",
            "A badge in the panel header names the window. The keywords have "
            "always followed the date range; when two ranges show the same "
            "rows, the account has no synced keyword history for the extra "
            "days, which the badge now makes visible.",
        ),
    ),
    Entry(
        date="2026-08-19",
        title="LinkedIn audience: summary cards and a CSV export",
        area="Campaign explorer",
        kind="new",
        summary=(
            "The LinkedIn audience panel now opens with the headline of "
            "whichever breakdown you are on, and the whole window downloads as "
            "a spreadsheet."
        ),
        details=(
            "Four cards per breakdown: the top category by impressions, the "
            "one with the most clicks, the best CTR among categories with real "
            "reach, and the total reported reach.",
            "The cards follow the tab — top company on Company, top industry "
            "on Industry.",
            "Export CSV downloads every breakdown in the synced window as one "
            "file, labelled with the window it came from.",
        ),
    ),
    Entry(
        date="2026-08-19",
        title="One comparison picker again, starting on No comparison",
        area="Client dashboards",
        kind="improved",
        summary=(
            "The Compare switch and its window dropdown are back to being a "
            "single picker listing No comparison, Previous period and Previous "
            "year — it just lands on No comparison instead of comparing by "
            "default."
        ),
        details=(
            "No comparison is the default, so a page still opens as plain "
            "numbers rather than an arrow under every figure.",
            "Whatever you had the switch set to carries over: switched off "
            "lands on No comparison, switched on keeps the window you were "
            "reading.",
        ),
    ),
    Entry(
        date="2026-08-19",
        title="Switch between previous period and previous year in one dropdown",
        area="Client dashboards",
        kind="improved",
        summary=(
            "The Use previous year link beside the Compare switch is now a "
            "dropdown reading period or year, so the window in use is visible "
            "rather than something you infer from the link's wording."
        ),
        details=(
            "The switch reads \"Compare to previous\" and the dropdown finishes "
            "it, so the pair reads as one phrase.",
        ),
    ),
    Entry(
        date="2026-08-19",
        title="Comparisons are one switch, and they start off",
        area="Client dashboards",
        kind="improved",
        summary=(
            "The Previous period / Previous year / No comparison picker is now a "
            "single Compare to previous period switch that starts off, so a page "
            "opens as plain numbers instead of an arrow under every figure."
        ),
        details=(
            "Turn it on and the vs-previous figures come back everywhere they were.",
            "Previous year is still there — a Use previous year action appears "
            "beside the switch once it is on.",
            "Both the on/off state and the window you picked are remembered per "
            "client in your own browser.",
            "Search Console's Δ Pos and Movement columns only appear while the "
            "switch is on, rather than sitting there full of dashes.",
        ),
    ),
    Entry(
        date="2026-08-19",
        title="Red and green now mean the movement is worth a look",
        area="Client dashboards",
        kind="improved",
        summary=(
            "A vs-previous figure is only coloured once the move reaches 10% — "
            "smaller ones keep their arrow and percentage in grey, so a colour on "
            "the page means something moved rather than something wobbled."
        ),
        details=(
            "CTR is no longer coloured by direction at all: it falls whenever "
            "impressions grow faster than clicks, which is good news if "
            "conversions came too.",
            "Comparison figures on campaign rows are smaller and lighter than the "
            "numbers they qualify.",
        ),
    ),
    Entry(
        date="2026-08-19",
        title="Campaign, ad group and ad rows look like three different levels",
        area="Campaign explorer",
        kind="improved",
        summary=(
            "An expanded campaign used to read as one flat list — the levels now "
            "step further in, get smaller and quieter as they nest, and carry a "
            "coloured rail down their left edge."
        ),
        details=(
            "A rule separates one campaign's block from the next once it is open.",
        ),
    ),
    Entry(
        date="2026-08-19",
        title="Campaign explorer rows carry their own vs-previous comparison",
        area="Campaign explorer",
        kind="improved",
        summary=(
            "Spend, impressions, clicks, CTR and conversions on each campaign "
            "row now show the same vs-previous figure the total row already "
            "did, instead of making you scroll to the bottom to see a trend."
        ),
    ),
    Entry(
        date="2026-08-19",
        title="Comparison period picker can turn comparisons off",
        area="Campaign explorer",
        kind="new",
        summary=(
            "The Previous period / Previous year picker has a third option, "
            "No comparison, which drops every vs-previous figure on the page "
            "for a cleaner read when comparing periods isn't useful."
        ),
    ),
    Entry(
        date="2026-08-19",
        title="Edit filters no longer seeds Product and Region for you",
        area="Campaign explorer",
        kind="fixed",
        summary=(
            "The Product/Region text in the Edit filters box was only ever "
            "meant as an example of the format — it was showing up as live "
            "filter chips for anyone who hadn't configured their own."
        ),
    ),
    Entry(
        date="2026-08-19",
        title="Average session duration is charted by week, not by day",
        area="Website Analytics",
        kind="improved",
        summary=(
            "The Daily view is gone from this card: a single day’s average "
            "moves several minutes on one long visit, so the daily bars read as "
            "noise rather than a trend."
        ),
        details=(
            "One bar per week, starting Monday, weighted by each day’s "
            "sessions.",
            "Hovering a bar names the week it covers and how many sessions it "
            "averages.",
            "Sessions over time keeps its Daily / Weekly toggle.",
        ),
    ),
    Entry(
        date="2026-08-19",
        title="Average session duration now has an over-time chart",
        area="Website Analytics",
        kind="improved",
        summary=(
            "The card that shipped this morning sits above a bar per day, so a "
            "run of short visits is visible as a trend instead of hiding inside "
            "one range-wide number."
        ),
        details=(
            "Daily / Weekly chips switch the bars; a week's figure is weighted "
            "by its days' sessions, so a dead Sunday doesn't count for as much "
            "as a busy Tuesday.",
            "The comparison period rides over the bars as a dashed line, and "
            "the legend gives each period's average.",
            "Hovering a bar shows that day's average and how many sessions it "
            "averages — a one-visit day reads as exactly that.",
        ),
    ),
    Entry(
        date="2026-08-19",
        title="Website Analytics shows average session duration",
        area="Website Analytics",
        kind="new",
        summary=(
            "A card above Demographics reports how long the average session "
            "lasted over the selected range, with the change against the "
            "comparison period."
        ),
        details=(
            "The average is weighted by how many sessions each landing page "
            "brought in, so a one-session page no longer counts as much as a "
            "busy one.",
            "Under a page-path scope it covers the sessions that started on a "
            "matching page.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="Campaign explorer now shows a vs-previous comparison",
        area="Campaign Explorer",
        kind="fixed",
        summary=(
            "The Previous period / Previous year picker at the top of the page "
            "had no effect on Campaign explorer — it now does."
        ),
        details=(
            "The summary cards (Spend, Impressions, Clicks, CTR, Conversions) "
            "show a colored vs-previous delta, matching the rest of the dashboard.",
            "The campaign table's Total row shows the same deltas under each "
            "column.",
            "Verified conv. (GA4) doesn't have a comparison yet — coming next.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="Hover a marked post to read it without leaving the chart",
        area="LinkedIn Organic",
        kind="improved",
        summary=(
            "The numbered posts on the engagement chart now hold their own "
            "detail: hover one and you get that post's copy and its numbers, "
            "so the list that sat above the chart \u2014 and the checkbox with "
            "it \u2014 are gone."
        ),
        details=(
            "Hovering anywhere else still gives the day: impressions, reach, "
            "posts published and their titles.",
            "A marked post shows its copy, reactions, comments, shares, clicks "
            "and engagement rate.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="Engagement over time reads posting volume, not just publish days",
        area="LinkedIn Organic",
        kind="improved",
        summary=(
            "Marking every publish day told you nothing for a client who posts "
            "daily \u2014 the chart filled with identical marks. It now shows how "
            "many posts went out each day as bars behind the lines, and names "
            "only the handful of posts that clearly outperformed."
        ),
        details=(
            "Posts / day sits along the bottom of the chart, so a heavy week is "
            "visible against the impressions it did or didn't earn.",
            "Up to five standout posts are numbered on the chart and listed "
            "above it with their impressions; a window where nothing stood out "
            "names nothing.",
            "Hovering any day still lists that day's posts, best first.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="The Users page leads with the roster",
        area="Users",
        kind="improved",
        summary=(
            "The user list is now the first thing on the page, each row's "
            "controls sit behind one “⋮” menu instead of four blue links, and "
            "creating a user folds out of the header — so the page reads as a "
            "roster you scan rather than a form you scroll past."
        ),
        details=(
            "Add user is a button in the roster header. The one-time invite "
            "link still appears at the top of the page when you create one.",
            "Reset password, invite link, role & access, and Deactivate all "
            "live in the row's ⋮ menu, each with a short note on what it does.",
            "The avatar and email sit together as one column, rows highlight "
            "on hover, and a live “N shown” count follows the filter box.",
            "Client groups rows got the same ⋮ menu, and a group that still "
            "has members says so instead of offering a delete that can't run.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="Email Performance sorts, and looks like the rest of the dashboard",
        area="Email Performance",
        kind="improved",
        summary=(
            "The page now uses the same cards and tables as Overview and "
            "Campaign Explorer, every column sorts, and the “Choose emails” "
            "list is big enough to read full email names."
        ),
        details=(
            "Click any column heading to sort — including a new Send date "
            "column; click again to reverse it. The table starts newest first.",
            "Rates sort by their real value, so an email with no deliveries "
            "never outranks one that performed.",
            "The “Choose emails” popover is roughly twice as wide and tall, "
            "and email names wrap instead of being cut off.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="Engagement over time shows the days posts went out",
        area="LinkedIn Organic",
        kind="new",
        summary=(
            "The engagement chart now marks every day a post was published, so "
            "you can see at a glance whether a spike in impressions follows a "
            "post — and what that post was."
        ),
        details=(
            "A dashed line and a dot sit on each publish day; a day with several "
            "posts shows the count inside the dot.",
            "Hovering a day lists the posts published on it, above the "
            "impressions and reach numbers.",
            "Use the checkbox above the chart to hide the markers.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="LinkedIn Organic syncs more than the 50 most recent posts",
        area="LinkedIn Organic",
        kind="fixed",
        summary=(
            "Every sync was quietly stopping after the first page of results, so "
            "a client's post history never went back further than their most "
            "recent 50 posts — which is why the longer date ranges kept showing "
            "the same numbers as the short ones. Syncs now read the whole history."
        ),
        details=(
            "Run a sync over a longer range to backfill a client's older posts.",
            "Nothing was lost — the older posts had simply never been collected.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="LinkedIn Organic's Performance card follows the date range",
        area="LinkedIn Organic",
        kind="fixed",
        summary=(
            "Posts, impressions, reach, reactions, comments and average "
            "engagement were being counted from the Top posts table, which only "
            "ever holds 50 rows — so on busy pages the tiles froze at the same "
            "numbers no matter which date range you picked. They are now counted "
            "over the whole selected period."
        ),
        details=(
            "Posts now shows every post in the period, not a count that stopped at 50.",
            "Average engagement is the average across all of those posts.",
            "Posts with no publish date no longer appear in every date range.",
            "Followers is still the current lifetime total; the green figure "
            "beneath it is the gain over the selected period.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="Bulk add is a popover, and the watchlist drops its Page column",
        area="Search Console",
        kind="fixed",
        summary=(
            "The keyword watchlist's bulk-add box was sitting open above the "
            "table for admins instead of waiting to be asked for. It is now a "
            "popover on the Bulk add button, and the Page column is pulled for "
            "now while it earns its width."
        ),
        details=(
            "“+ Add keyword” now reads as the primary action it is.",
            "The popover closes on Escape, on Cancel, or on a click outside it.",
            "The page saved against each keyword is kept — nothing was "
            "discarded, and the column can come back.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="Put the keyword watchlist in the order you want to read it",
        area="Search Console",
        kind="improved",
        summary=(
            "Watchlist rows can be dragged into any order, and the list now opens "
            "in that order instead of by impressions — so the keywords this "
            "quarter is about can sit at the top."
        ),
        details=(
            "Drag the handle at the left of a row, or focus it and use the arrow "
            "keys (Home and End send a row to the top or bottom).",
            "Sorting by a column is still just a view: the ⇅ header puts the "
            "list back in your order.",
            "A new row is added at the top, where you can see it.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="Edit the keyword watchlist in the list itself, and paste in a batch",
        area="Search Console",
        kind="improved",
        summary=(
            "The watchlist no longer has a separate edit panel: click a keyword "
            "or page in the table to change it, and a Bulk add box takes a "
            "pasted batch of keywords at once."
        ),
        details=(
            "“+ Add keyword” puts a new row straight into the list, "
            "waiting for its keyword.",
            "Clearing a keyword removes that row; each row has an × on hover.",
            "Bulk add takes one row per line as “keyword, page”, and a "
            "line of comma-separated keywords with no page adds one row each. It "
            "says how many it added and how many were already there.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="Search Console fills in gaps in its own history",
        area="Search Console",
        kind="fixed",
        summary=(
            "A sync interrupted partway — a deploy landing mid-backfill is the "
            "usual way — used to leave a block of missing days that nothing ever "
            "went back for: the next sync looked only at the newest date it "
            "found, decided the client was current, and reported success while "
            "weeks of the middle stayed blank. Search Console now checks every "
            "day in the window and refills whatever is missing, so a broken "
            "sync repairs itself on the next run."
        ),
        details=(
            "Two clients were missing several weeks each — those days refill "
            "automatically now.",
            "High-traffic days were also cut off at 5,000 keywords each, so "
            "clicks and impressions on the Queries table were undercounted; full "
            "days are collected from here on.",
            "A day Search Console genuinely has no data for is remembered as "
            "empty instead of being re-checked every day.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="Slack hears back when a feature request is done",
        area="Admin · Feature requests",
        kind="new",
        summary=(
            "Marking a feature request done now replies in that request’s own Slack "
            "thread, so everyone who followed the ask finds out it shipped without "
            "checking the admin inbox."
        ),
        details=(
            "The reply names who marked it done and links back to the inbox.",
            "Requests raised before this shipped have no thread to reply to, so "
            "their close-out posts to the channel and quotes the original ask.",
            "Only the first click posts — marking an already-done request changes "
            "nothing and sends nothing.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="The Search Console property picker says why it came up empty",
        area="Connectors · Search Console",
        kind="fixed",
        summary=(
            "When the wizard could not list a client's Search Console properties "
            "— usually the agency Google connection needing to be reconnected "
            "— step 2 said “No accounts found for this connection,” the same "
            "thing it says when the login genuinely has none. It now shows the "
            "reason Google gave, which is the screen you go to when a client's "
            "keyword data has stopped updating."
        ),
        details=(
            "Names the fix: reconnect under Admin → Connect Google Search Console.",
            "A login with no properties still reads as an empty list, not an error.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="A Search Console sync that fetched nothing no longer says “completed”",
        area="Connectors · Search Console",
        kind="fixed",
        summary=(
            "When Search Console refused every day of a sync — usually because the "
            "connected property is wrong or the Google connection lost access — "
            "Sync history still logged it green, as “completed, 0 rows,” with an "
            "empty Error column. Those runs now show as failed with the reason "
            "Google gave, so a client stuck on stale keyword data is visible on "
            "the Connectors page instead of only in the numbers."
        ),
        details=(
            "The Error column names the failing days and Google’s message, e.g. a "
            "403 on the property.",
            "A genuine no-op — Search Console already up to date — still reports "
            "completed with 0 rows.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="Branded and Target queries now share one full-width card",
        area="Search Console",
        kind="improved",
        summary=(
            "Branded and Target queries used to sit side by side, each squeezed "
            "into half the page. They are one card with two tabs now — the same "
            "tabs Website Analytics uses for Pages / Landing Pages — so the "
            "group you are reading gets the whole width and a readable "
            "avg-position chart."
        ),
        details=(
            "Click Branded queries or Target queries to switch; the match count "
            "sits on each tab.",
            "Admins: the Edit control for the branded/target term lists follows "
            "whichever tab is open.",
        ),
    ),
    Entry(
        date="2026-08-18",
        title="Search Console panels can be reordered and hidden",
        area="Search Console",
        kind="new",
        summary=(
            "Search Console now has the same layout editor as Overview and "
            "Campaign Explorer, so a client's tab can lead with whatever matters "
            "most to them instead of a fixed running order."
        ),
        details=(
            "Admins: hover Search Console in the sidebar, open the ⋮ menu and "
            "choose Edit layout.",
            "Drag to reorder, or Hide / Show the summary, Top queries & pages, "
            "the Keyword watchlist, Branded & Target Keywords and Organic Search "
            "Intelligence.",
            "Changes save per client and apply for everyone on that portal; "
            "hidden panels are not sent to clients at all.",
        ),
    ),
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
