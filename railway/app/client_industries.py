"""The agency's client industry taxonomy — one list, used everywhere.

Every account carries **zero or more** industry keys (stored on
``dashboard_clients.industry`` as a comma-separated list of keys). The point of
the tag is comparison: once a client is bucketed, the admin Benchmarks page can
answer "what does a typical industrial-manufacturing client see for paid CTR /
traffic / followers" instead of only ever showing one client at a time.

Multi-tagging exists because plenty of B2B accounts genuinely straddle two
markets — a clinical-workflow SaaS vendor is *both* Health & Life Sciences and
Technology & Software, and forcing a single pick made one of those benchmarks
wrong. A client tagged with two industries counts in **both** buckets (and once,
not twice, in the agency-wide row).

Design rules, so this stays contained as the roster grows:

* **This module is the only source of truth.** Renderers, services, and the
  admin form all read :data:`INDUSTRIES`; nothing hardcodes a label. Adding a
  bucket is a one-line edit here — no migration, no renderer change.
* **Keys are stable, labels are not.** The key is what lands in Postgres, so it
  never changes once shipped; the label is display text and can be reworded
  freely. Renaming a *key* would orphan existing rows, so don't.
* **Broad buckets on purpose.** ~15 recognizable groupings, sized so most
  Sagefrog B2B accounts land in exactly one without a judgement call. A bucket
  that would hold one client forever belongs in ``other`` instead — small
  buckets make useless benchmarks (see the ``n`` column on the Benchmarks page).
* **Unset is not "other".** An account with no tag reads as *Unassigned* and is
  excluded from per-industry rows (it still counts in the agency-wide row);
  ``other`` is a deliberate "we looked, it fits nothing" choice. Keeping them
  distinct is what makes tagging coverage measurable.
* **Tag what the client actually is, not everything it touches.** Two industries
  is the common case for a straddling account; tagging an account into five
  buckets makes five benchmarks less true, not one account better described.
"""

from __future__ import annotations

from collections.abc import Iterable

# (key, label) in display order. Keys are permanent; labels are free to reword.
INDUSTRIES: tuple[tuple[str, str], ...] = (
    ("healthcare_life_sciences", "Health & Life Sciences"),
    ("industrial_manufacturing", "Industrial Manufacturing"),
    ("technology_software", "Technology & Software"),
    ("financial_services", "Financial Institutions & Insurance"),
    ("business_services", "Business & Professional Services"),
    ("aec", "Architecture, Engineering & Construction"),
    ("energy_utilities", "Energy & Utilities"),
    ("transportation_logistics", "Transportation & Logistics"),
    ("chemicals_materials", "Chemicals & Materials"),
    ("consumer_retail", "Consumer Products & Retail"),
    ("real_estate_hospitality", "Real Estate & Hospitality"),
    ("education_nonprofit", "Education & Nonprofit"),
    ("government_public", "Government & Public Sector"),
    ("media_communications", "Media & Communications"),
    ("agriculture_food", "Agriculture & Food"),
    ("other", "Other"),
)

# Label shown for an account that has not been tagged yet. Distinct from
# "Other" — see the module docstring.
UNASSIGNED_LABEL = "Unassigned"

# How a multi-industry tag is stored in the single ``dashboard_clients.industry``
# TEXT column, and how the labels read when joined for display. Storing a list in
# the existing column (rather than adding a second one) means an account tagged
# before multi-select shipped is already a valid one-element list — no migration,
# no backfill, and no second column to keep in sync.
SEPARATOR = ","
LABEL_JOINER = " · "

_BY_KEY: dict[str, str] = dict(INDUSTRIES)
_ORDER: dict[str, int] = {key: i for i, (key, _label) in enumerate(INDUSTRIES)}


def keys() -> tuple[str, ...]:
    """Every valid industry key, in display order."""
    return tuple(key for key, _label in INDUSTRIES)


def choices() -> tuple[tuple[str, str], ...]:
    """(key, label) pairs for building a <select>."""
    return INDUSTRIES


def is_valid(key: str | None) -> bool:
    return (key or "").strip().lower() in _BY_KEY


def normalize(raw: str | None) -> str | None:
    """Coerce user/DB input to a known key, or None for unset/unrecognized.

    Unknown keys normalize to None rather than raising: an industry that was
    removed from the list above should degrade to "Unassigned" on the page, not
    500 the admin panel.
    """
    key = (raw or "").strip().lower()
    return key if key in _BY_KEY else None


def label_for(key: str | None) -> str:
    """Display label for a single key, falling back to :data:`UNASSIGNED_LABEL`."""
    return _BY_KEY.get((key or "").strip().lower(), UNASSIGNED_LABEL)


# ── Multi-industry helpers ───────────────────────────────────────────────────
# An account carries a set of keys. These are the only functions that know how
# that set is spelled, so storage format and display joining stay in one place.


def normalize_many(raw: str | Iterable[str | None] | None) -> tuple[str, ...]:
    """Coerce a stored/posted tag into a deduped tuple of known keys.

    Accepts the stored string (``"a,b"``), a list of form values, or ``None``.
    Unknown and empty entries drop out for the same reason :func:`normalize`
    returns None for them — a retired key should read as untagged, not 500 the
    admin panel. The result is ordered by the taxonomy, not by input order, so a
    tag renders identically no matter which order the boxes were ticked.
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        parts: Iterable[str | None] = raw.split(SEPARATOR)
    else:
        # A form post can send one field per checkbox *and* comma-joined values;
        # splitting each entry handles both without the caller caring.
        parts = [piece for entry in raw for piece in str(entry or "").split(SEPARATOR)]

    seen: set[str] = set()
    for part in parts:
        key = normalize(part)
        if key:
            seen.add(key)
    return tuple(sorted(seen, key=lambda k: _ORDER[k]))


def serialize(keys: str | Iterable[str | None] | None) -> str | None:
    """Storage form for a set of keys — ``"a,b"``, or None when nothing is set.

    None rather than ``""`` so an untagged row stays SQL-NULL, which is what
    ``WHERE industry IS NOT NULL`` and the coverage count both read.
    """
    normalized = normalize_many(keys)
    return SEPARATOR.join(normalized) if normalized else None


def labels_for(keys: str | Iterable[str | None] | None) -> tuple[str, ...]:
    """Display labels for a set of keys, in taxonomy order. Empty when untagged."""
    return tuple(_BY_KEY[key] for key in normalize_many(keys))


def label_list(keys: str | Iterable[str | None] | None) -> str:
    """One display string for a whole tag — ``"A · B"``, or *Unassigned*."""
    labels = labels_for(keys)
    return LABEL_JOINER.join(labels) if labels else UNASSIGNED_LABEL
