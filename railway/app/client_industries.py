"""The agency's client industry taxonomy — one list, used everywhere.

Every account carries at most one industry key (stored on
``dashboard_clients.industry``). The point of the tag is comparison: once a
client is bucketed, the admin Benchmarks page can answer "what does a typical
industrial-manufacturing client see for paid CTR / traffic / followers" instead
of only ever showing one client at a time.

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
"""

from __future__ import annotations

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

_BY_KEY: dict[str, str] = dict(INDUSTRIES)


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
    """Display label for a key, falling back to :data:`UNASSIGNED_LABEL`."""
    return _BY_KEY.get((key or "").strip().lower(), UNASSIGNED_LABEL)
