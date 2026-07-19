# Consent scanner test fixture

A self-contained demo marketing site used to exercise the Consent & Tracking
Health scanner against **real browser automation** (not mocks).

It implements a realistic consent setup:

- Google **Consent Mode** default *denied* before any choice.
- A consent banner with **Reject All** / **Accept All** buttons.
- GA4, Google Tag Manager, Meta Pixel, LinkedIn Insight and HubSpot references.
- Consent-gated behaviour: after *Accept All*, GA4 sets `_ga` and sends an
  identified hit; after *Reject All* it keeps sending only cookieless pings.

`index.html` deliberately contains a **pre-consent Meta Pixel leak** (it sets
`_fbp` and fires `facebook.com/tr` on load, before the visitor chooses) so the
scanner produces a genuine *critical* finding. `pricing.html` is well-behaved
(no pre-consent identifiers) so the two pages contrast in the report.

## Run the scanner against it

```bash
cd railway/app
python -m http.server 8199 --directory tests/fixtures/consent_site &
CONSENT_SCANNER_NO_SANDBOX=1 CONSENT_ALLOW_PRIVATE_HOSTS=1 python -c "
import json, consent_scanner, consent_service as svc
raw = consent_scanner.scan_pages([
    'http://127.0.0.1:8199/index.html',
    'http://127.0.0.1:8199/pricing.html'])
res = svc.build_result(raw, expectations=svc.default_expectations())
print('health:', res['health'])
print('headline:', res['summary']['headline'])
"
```

Expected: `health: fail` — Meta Pixel identifiers set/sent before consent, while
the GA4 Consent Mode cookieless pings are correctly **not** flagged.
