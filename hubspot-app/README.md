# HubSpot app (projects platform)

Config for the public OAuth app that client portals install when they connect
HubSpot on the Connectors page. Lives here so the scopes stay next to the code
that depends on them — `oauth_flows.HUBSPOT_SCOPES` /
`HUBSPOT_OPTIONAL_SCOPES` and this file must always agree, because HubSpot
rejects an install URL that requests a scope the app doesn't declare.

Legacy (click-to-create) apps are no longer offered in new developer portals, so
this app is created with the CLI against the Sagefrog Solutions Partner account.

## Create / update

```bash
npm install -g @hubspot/cli@latest
hs account auth --default   # authenticate the Sagefrog partner portal (dev account 455263)
hs account info             # confirm it is the developer account before uploading
hs project upload           # from this directory
hs project open             # -> Project components -> the app -> Auth tab
```

`hs init` no longer exists; the CLI moved to a global config at
`~/.hscli/config.yml`.

Copy the **Client ID** and **Client secret** from that Auth tab into Railway as
`HUBSPOT_CLIENT_ID` and `HUBSPOT_CLIENT_SECRET`. The connector reports "not
ready" until both are set (`oauth_flows.connector_env_status`).

## Scopes

| Scope | Why | Where |
|---|---|---|
| `oauth` | basic OAuth | required |
| `crm.objects.contacts.read` | contacts search (lead tracking) | required |
| `crm.objects.deals.read` | deals search | required |
| `content` | `GET /marketing/v3/emails?includeStats=true` | **optional** |

`content` must stay **optional**. Only Marketing Hub tiers expose it, so making
it required would block every lower-tier client portal from connecting at all.
Portals that don't grant it just 403 on the email sync, which is then skipped.

## Redirect URL

Must byte-for-byte match `<PUBLIC_BASE_URL>/oauth/hubspot/callback`.

`PUBLIC_BASE_URL` is **not** set in Railway, so `public_base_url()` resolves via
`RAILWAY_PUBLIC_DOMAIN` — currently `sagefrog-production.up.railway.app`, the
service's only public domain (verified 2026-08-26). The committed `redirectUrls`
matches that. If a custom domain is ever added, or `PUBLIC_BASE_URL` is set, this
file has to be updated and re-uploaded or every HubSpot connect attempt fails at
the consent screen.

## Layout

`hs project upload` reads `hsproject.json` from the working directory and the
app config from `src/app/app-hsmeta.json`. `hsproject.json`'s `name` is
permanent once uploaded. Regenerate a reference copy of this layout with:

```bash
hs project create --project-base app --distribution marketplace --auth oauth
```

`CLAUDE.md` and `HUBSPOT_PROJECTS.md` are HubSpot's own generated reference for
this platform version; they document the config schema and are worth reading
before changing `app-hsmeta.json`.
