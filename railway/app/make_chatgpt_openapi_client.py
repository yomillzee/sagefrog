"""Build client-scoped OpenAPI for Custom GPTs from openapi-chatgpt-min.json."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
MIN_SCHEMA = ROOT / "openapi-chatgpt-min.json"

CLIENTS: dict[str, dict[str, Any]] = {
    "penn": {
        "title": "Penn Community Bank — EOS Marketing Analytics",
        "description": (
            "Penn Community Bank only. Google Ads, LinkedIn, Meta, and GA4 via agency API. "
            "Never use other clients' accounts or GA4 projects. Auth: X-API-Key (Railway API_KEY)."
        ),
        "label": "Penn Community Bank",
        "name_match": "Penn",
        "ga4_client_key": "penn",
        "bq_project_id": "penn-community-b-1699391543298",
        "bq_dataset_id": "analytics_313855909",
        "google_ads_customer_id": None,
        "linkedin_account_id": None,
        "meta_account_id": None,
    },
}

# Endpoints that list or aggregate across all agency clients — omit from client GPTs.
DROP_PATHS = {
    "/google-ads/search-many",
    "/google-ads/summary-all",
    "/ga4/clients",
}


def _patch_account_param(
    op: dict[str, Any],
    *,
    param_name: str,
    description: str,
    enum_value: str | None,
) -> None:
    for param in op.get("parameters") or []:
        if param.get("name") != param_name:
            continue
        param["description"] = description
        if enum_value:
            param["schema"] = {
                "type": "string",
                "enum": [enum_value],
            }


def _patch_body_property(
    schema: dict[str, Any],
    prop: str,
    *,
    description: str | None = None,
    enum_value: str | None = None,
    remove: bool = False,
) -> None:
    props = schema.get("properties") or {}
    if prop not in props:
        return
    if remove:
        props.pop(prop, None)
        return
    if description is not None:
        props[prop]["description"] = description
    if enum_value is not None:
        props[prop]["enum"] = [enum_value]
        props[prop]["default"] = enum_value


def build_client_schema(client_key: str) -> dict[str, Any]:
    cfg = CLIENTS[client_key]
    schema = copy.deepcopy(json.loads(MIN_SCHEMA.read_text(encoding="utf-8")))

    schema["info"]["title"] = cfg["title"]
    schema["info"]["description"] = cfg["description"]

    paths = schema.get("paths") or {}
    for drop in DROP_PATHS:
        paths.pop(drop, None)

    label = cfg["label"]
    name_match = cfg["name_match"]
    penn_only = (
        f"Penn Community Bank only. After listing accounts, use only rows whose name "
        f"contains '{name_match}'. Refuse other clients."
    )

    # Google Ads
    if op := paths.get("/google-ads/accounts", {}).get("get"):
        op["summary"] = f"List Google Ads accounts — use {label} only"
        op["description"] = penn_only

    gid = cfg.get("google_ads_customer_id")
    for path_key, op_id in (
        ("/google-ads/search", "googleAdsSearch"),
        ("/google-ads/warehouse/sync", "googleAdsWarehouseSync"),
    ):
        op = paths.get(path_key, {}).get("post")
        if not op:
            continue
        body_schema = op["requestBody"]["content"]["application/json"]["schema"]
        desc = (
            f"Google Ads customer ID for {label} only."
            if not gid
            else f"Google Ads customer ID for {label}: {gid}"
        )
        _patch_body_property(body_schema, "customer_id", description=desc, enum_value=gid)

    if op := paths.get("/google-ads/youtube-videos", {}).get("post"):
        body_schema = op["requestBody"]["content"]["application/json"]["schema"]
        _patch_body_property(
            body_schema,
            "customer_id",
            description=f"YouTube videos for {label} Google Ads account only.",
            enum_value=gid,
        )
        op["summary"] = f"List YouTube video assets with thumbnails — {label} only"
        op["description"] = (
            f"{penn_only} Returns youtube_watch_url, youtube_embed_url, and "
            f"youtube_thumbnail_url for each video asset."
        )

    # LinkedIn
    linkedin_perf_paths = (
        "/linkedin/performance",
        "/linkedin/campaign-groups/performance",
        "/linkedin/creatives/performance",
        "/linkedin/videos",
    )
    for path_key in linkedin_perf_paths:
        op = paths.get(path_key, {}).get("get")
        if not op:
            continue
        if path_key == "/linkedin/accounts":
            continue
        lid = cfg.get("linkedin_account_id")
        desc = (
            f"LinkedIn ad account ID for {label} (digits only)."
            if not lid
            else f"LinkedIn ad account ID for {label}: {lid}"
        )
        _patch_account_param(op, param_name="account_id", description=desc, enum_value=lid)

    if op := paths.get("/linkedin/accounts", {}).get("get"):
        op["summary"] = f"List LinkedIn ad accounts — use {label} only"
        op["description"] = penn_only

    for path_key in ("/linkedin/campaign-groups",):
        op = paths.get(path_key, {}).get("get")
        if not op:
            continue
        lid = cfg.get("linkedin_account_id")
        desc = (
            f"LinkedIn ad account ID for {label} (digits only)."
            if not lid
            else f"LinkedIn ad account ID for {label}: {lid}"
        )
        _patch_account_param(op, param_name="account_id", description=desc, enum_value=lid)

    if op := paths.get("/linkedin/warehouse/sync", {}).get("post"):
        body_schema = op["requestBody"]["content"]["application/json"]["schema"]
        lid = cfg.get("linkedin_account_id")
        _patch_body_property(
            body_schema,
            "account_id",
            description=f"LinkedIn account ID for {label} only.",
            enum_value=lid,
        )

    meta_perf_paths = (
        "/meta/performance",
        "/meta/adsets/performance",
        "/meta/videos",
    )
    for path_key in meta_perf_paths:
        op = paths.get(path_key, {}).get("get")
        if not op:
            continue
        mid = cfg.get("meta_account_id")
        desc = (
            f"Meta ad account ID for {label} (digits only)."
            if not mid
            else f"Meta ad account ID for {label}: {mid}"
        )
        _patch_account_param(op, param_name="account_id", description=desc, enum_value=mid)

    if op := paths.get("/meta/accounts", {}).get("get"):
        op["summary"] = f"List Meta ad accounts — use {label} only"
        op["description"] = penn_only

    if op := paths.get("/meta/warehouse/sync", {}).get("post"):
        body_schema = op["requestBody"]["content"]["application/json"]["schema"]
        mid = cfg.get("meta_account_id")
        _patch_body_property(
            body_schema,
            "account_id",
            description=f"Meta ad account ID for {label} only.",
            enum_value=mid,
        )

    # GA4
    bq_project = cfg["bq_project_id"]
    bq_dataset = cfg["bq_dataset_id"]
    ga4_key = cfg["ga4_client_key"]
    fq_table = f"`{bq_project}.{bq_dataset}.events_*`"

    if op := paths.get("/ga4/warehouse/sync", {}).get("post"):
        op["description"] = (
            f"Sync GA4 daily metrics for {label} only. Always use client_key '{ga4_key}'."
        )
        body_schema = op["requestBody"]["content"]["application/json"]["schema"]
        _patch_body_property(
            body_schema,
            "client_key",
            description=f"Must be '{ga4_key}' for {label}.",
            enum_value=ga4_key,
        )
        _patch_body_property(body_schema, "bq_project_id", remove=True)
        _patch_body_property(body_schema, "bq_dataset_id", remove=True)

    if op := paths.get("/ga4/query", {}).get("post"):
        op["description"] = (
            f"BigQuery SQL for {label} GA4 export only. Tables must be under {fq_table} "
            f"(project {bq_project}, dataset {bq_dataset}). Do not query other projects."
        )

    if op := paths.get("/ga4/env", {}).get("get"):
        op["description"] = f"GA4 env check for {label} (Penn BigQuery project/dataset)."

    # Warehouse read — steer filters
    if op := paths.get("/warehouse/metrics", {}).get("get"):
        for param in op.get("parameters") or []:
            if param.get("name") == "account_id":
                param["description"] = (
                    f"Account ID for {label} only (Google customer ID, LinkedIn/Meta account ID, "
                    f"or GA4 property {cfg.get('bq_dataset_id', '').replace('analytics_', '')})."
                )

    schema["paths"] = paths
    return schema


def write_instructions(client_key: str, out_path: Path) -> None:
    cfg = CLIENTS[client_key]
    label = cfg["label"]
    ga4_key = cfg["ga4_client_key"]
    bq_project = cfg["bq_project_id"]
    bq_dataset = cfg["bq_dataset_id"]
    lines = [
        f"# Custom GPT instructions — {label}",
        "",
        f"You are the marketing analytics assistant for **{label}** only.",
        "",
        "## Scope (strict)",
        "",
        f"- You only report on **{label}**. Never pull, mention, or infer metrics for other clients.",
        f"- If the user asks about another brand, say this GPT is scoped to {label} only.",
        "",
        "## How to pick accounts",
        "",
        "1. Call the platform **accounts** action when you need an account ID.",
        f"2. Choose the account whose name clearly matches **{cfg['name_match']}**.",
        "3. If no matching account appears, stop and tell the user — do not guess another account.",
        "",
        "## Ad hierarchy — do not mix platform terms",
        "",
        "| Level | LinkedIn | Meta | Google Ads | API action |",
        "|-------|----------|------|------------|------------|",
        "| Account | Ad account | Ad account | Customer ID | `*Accounts` |",
        "| Group/folder | **Campaign group** | *(none)* | *(none)* | `linkedinCampaignGroups*` only |",
        "| Campaign | Campaign | Campaign | Campaign | `linkedinPerformance`, `metaPerformance`, GAQL |",
        "| Ad/creative | **Creative** (ad) | **Ad set** | **Ad group** | `linkedinCreativesPerformance`, `metaAdsetsPerformance` |",
        "",
        "**LinkedIn has no ad set.** Do not treat campaign groups or creatives as ad sets.",
        "**Never map Meta ad set to LinkedIn campaign group.** For LinkedIn group spend use "
        "`linkedinCampaignGroupsPerformance`, not Meta.",
        "",
        "## Dashboard rules (LinkedIn)",
        "",
        "- **Campaign dashboard** → `linkedinPerformance` only. Rows have `entity_level=campaign`.",
        "- **Campaign group dashboard** → `linkedinCampaignGroupsPerformance` only (`entity_level=campaign_group`).",
        "- **Ad/creative dashboard** → `linkedinCreativesPerformance` (`entity_level=creative`).",
        "- Always join and aggregate by **`id`**, never by **`name`** (names can repeat across levels).",
        "- Do **not** sum campaign groups + campaigns + creatives — that double-counts.",
        "- Filter creatives to one campaign with optional `campaign_id` on `linkedinCreativesPerformance`.",
        "",
        "## Dashboard rules (Meta)",
        "",
        "- **Campaign dashboard** → `metaPerformance` only. Rows have `entity_level=campaign`.",
        "- **Ad set dashboard** → `metaAdsetsPerformance` only (`entity_level=adset`).",
        "- Always join and aggregate by **`id`**, never by **`name`**.",
        "- Do **not** sum campaigns + ad sets — that double-counts.",
        "- Filter ad sets to one campaign with optional `campaign_id` on `metaAdsetsPerformance`.",
        "",
        "## Platform rules",
        "",
        "### Google Ads",
        "- Use `googleAdsAccounts`, then only the Penn customer ID.",
        "- Do not use multi-account search or summary-all actions (not available in this GPT).",
        "- **Video creatives (YouTube):** use `googleAdsYoutubeVideos`. Each row has `youtube_watch_url`, `youtube_embed_url`, and `youtube_thumbnail_url` — show thumbnails in dashboards when the user asks for video previews.",
        "- Set `include_account_assets: true` (default) to catch videos not tied to a live ad view.",
        "- **Meta video previews:** `metaVideos` — returns `thumbnail_url` and `video_url` per ad.",
        "- **LinkedIn video previews:** `linkedinVideos` — returns `thumbnail_url` and `video_url` per creative.",
        "- For all platforms, show `thumbnail_url` in dashboards when the user asks for video previews.",
        "",
        "### LinkedIn",
        f"- Use `linkedinAccounts`, then `linkedinPerformance` with {cfg['name_match']}'s account ID for **campaign**-level metrics only.",
        "- For **campaign group** (folder above campaigns): `linkedinCampaignGroupsPerformance` — not the same as Meta ad set.",
        "- For **ads/creatives** (below campaign): `linkedinCreativesPerformance`. LinkedIn has no ad set.",
        "- `linkedinCampaignGroups` lists group names/IDs only; if empty, use performance anyway.",
        "- When building dashboards, use `entity_level` and row `id` — never merge rows from different actions by name.",
        "- For **video/creative previews**: `linkedinVideos` (`thumbnail_url`, `video_url`). Optional `campaign_id` filter.",
        "",
        "### Meta (Facebook/Instagram ads)",
        "- Use `metaAccounts`, then `metaPerformance` with Penn's account ID for **campaign**-level metrics only.",
        "- For **ad sets** (below campaign): `metaAdsetsPerformance`. Optional `campaign_id` to filter.",
        "- When building dashboards, use `entity_level` and row `id` — never merge rows from different actions by name.",
        "- For **video/creative previews**: `metaVideos` (`thumbnail_url`, `video_url`). Requires **ads_read** on the account — if it fails, call `metaTestAdsAccess` first.",
        "",
        "### GA4 / BigQuery",
        f"- For warehouse sync, always pass `\"client_key\": \"{ga4_key}\"` in `ga4WarehouseSync`.",
        f"- For SQL, only query `{bq_project}.{bq_dataset}` (e.g. `{bq_project}.{bq_dataset}.events_*`).",
        f"- Do not call `ga4Clients` or query sagefrog, synergistix, or other projects.",
        "",
        "### Warehouse history",
        "- Use `warehouseMetrics` with `from_date` / `to_date` and the Penn account ID for the source.",
        "",
        "## Date ranges",
        "",
        "Use LAST_7_DAYS, LAST_30_DAYS, LAST_90_DAYS, or LAST_180_DAYS unless the user specifies dates.",
        "",
    ]
    if cfg.get("google_ads_customer_id"):
        lines.append(f"- Penn Google Ads customer ID: `{cfg['google_ads_customer_id']}`")
    if cfg.get("linkedin_account_id"):
        lines.append(f"- Penn LinkedIn account ID: `{cfg['linkedin_account_id']}`")
    if cfg.get("meta_account_id"):
        lines.append(f"- Penn Meta ad account ID: `{cfg['meta_account_id']}`")
    lines.extend(
        [
            f"- Penn GA4: client_key `{ga4_key}`, dataset `{bq_dataset}`",
            "",
        ]
    )
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    client_key = (sys.argv[1] if len(sys.argv) > 1 else "penn").strip().lower()
    if client_key not in CLIENTS:
        known = ", ".join(sorted(CLIENTS))
        raise SystemExit(f"Unknown client '{client_key}'. Known: {known}")

    schema = build_client_schema(client_key)
    out_json = ROOT / f"openapi-chatgpt-{client_key}.json"
    out_json.write_text(json.dumps(schema, indent=2), encoding="utf-8")

    out_md = ROOT / f"gpt-instructions-{client_key}.md"
    write_instructions(client_key, out_md)

    print(f"Wrote {out_json.name} ({len(json.dumps(schema))} bytes)")
    print(f"Wrote {out_md.name}")


if __name__ == "__main__":
    main()
