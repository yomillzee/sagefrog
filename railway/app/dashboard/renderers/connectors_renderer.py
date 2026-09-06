"""HTML renderer for the Connectors directory and setup wizard pages."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote as _url_quote

import connector_config_store
import connectors  # noqa: F401 — triggers handler registration
from connectors.base import CONNECTOR_ORDER, ConnectorHandler, all_handlers
from dashboard.renderers.base_layout import render_client_shell_page
from dashboard.utils.formatting import esc as _esc

# ──────────────────────────────────────────────────────────────────────────────
# SVG icons per platform
# ──────────────────────────────────────────────────────────────────────────────

_PLATFORM_ICONS: dict[str, str] = {
    # LinkedIn
    "linkedin_ads": (
        '<svg viewBox="0 0 24 24" fill="#0A66C2" aria-hidden="true">'
        '<path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.14.923-2.064 2.063-2.064 1.14 0 2.064.925 2.064 2.064 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>'
    ),
    # LinkedIn Organic — same glyph as Ads, outlined so the two are distinguishable
    "linkedin_organic": (
        '<svg viewBox="0 0 24 24" aria-hidden="true">'
        '<rect x="1.6" y="1.6" width="20.8" height="20.8" rx="5" fill="none" stroke="#0A66C2" stroke-width="1.9"/>'
        '<g fill="#0A66C2" transform="translate(12 12) scale(.75) translate(-12 -12)">'
        '<path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286z"/>'
        '<path d="M5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.14.923-2.064 2.063-2.064 1.14 0 2.064.925 2.064 2.064 0 1.139-.925 2.065-2.064 2.065z"/>'
        '<path d="M7.119 20.452H3.555V9h3.564v11.452z"/>'
        '</g></svg>'
    ),
    # Bluesky
    "bluesky": (
        '<svg viewBox="0 0 24 24" fill="#0285FF" aria-hidden="true">'
        '<path d="M5.202 2.857C7.954 4.922 10.913 9.11 12 11.358c1.087-2.247 4.046-6.436 6.798-8.501C20.783 1.366 24 .213 24 3.883c0 .732-.42 6.156-.667 7.037-.856 3.061-3.978 3.842-6.755 3.37 4.854.826 6.089 3.562 3.422 6.299-5.065 5.196-7.28-1.304-7.847-2.97-.104-.305-.152-.448-.153-.327 0-.121-.05.022-.153.327-.568 1.666-2.782 8.166-7.847 2.97-2.667-2.737-1.432-5.473 3.422-6.3-2.777.473-5.899-.308-6.755-3.369C.42 10.04 0 4.615 0 3.883c0-3.67 3.217-2.517 5.202-1.026"/></svg>'
    ),
    # Meta
    "meta_ads": (
        '<svg viewBox="0 0 24 24" fill="#0467DF" aria-hidden="true">'
        '<path d="M6.915 4.03c-1.968 0-3.683 1.28-4.871 3.113C.704 9.208 0 11.883 0 14.449c0 .706.07 1.369.21 1.973a6.624 6.624 0 0 0 .265.86 5.297 5.297 0 0 0 .371.761c.696 1.159 1.818 1.927 3.593 1.927 1.497 0 2.633-.671 3.965-2.444.76-1.012 1.144-1.626 2.663-4.32l.756-1.339.186-.325c.061.1.121.196.183.3l2.152 3.595c.724 1.21 1.665 2.556 2.47 3.314 1.046.987 1.992 1.22 3.06 1.22 1.075 0 1.876-.355 2.455-.843a3.743 3.743 0 0 0 .81-.973c.542-.939.861-2.127.861-3.745 0-2.72-.681-5.357-2.084-7.45-1.282-1.912-2.957-2.93-4.716-2.93-1.047 0-2.088.467-3.053 1.308-.652.57-1.257 1.29-1.82 2.05-.69-.875-1.335-1.547-1.958-2.056-1.182-.966-2.315-1.303-3.454-1.303zm10.16 2.053c1.147 0 2.188.758 2.992 1.999 1.132 1.748 1.647 4.195 1.647 6.4 0 1.548-.368 2.9-1.839 2.9-.58 0-1.027-.23-1.664-1.004-.496-.601-1.343-1.878-2.832-4.358l-.617-1.028a44.908 44.908 0 0 0-1.255-1.98c.07-.109.141-.224.211-.327 1.12-1.667 2.118-2.602 3.358-2.602zm-10.201.553c1.265 0 2.058.791 2.675 1.446.307.327.737.871 1.234 1.579l-1.02 1.566c-.757 1.163-1.882 3.017-2.837 4.338-1.191 1.649-1.81 1.817-2.486 1.817-.524 0-1.038-.237-1.383-.794-.263-.426-.464-1.13-.464-2.046 0-2.221.63-4.535 1.66-6.088.454-.687.964-1.226 1.533-1.533a2.264 2.264 0 0 1 1.088-.285z"/></svg>'
    ),
    # Google Ads
    "google_ads": (
        '<svg viewBox="0 0 24 24" fill="#4285F4" aria-hidden="true">'
        '<path d="M3.9998 22.9291C1.7908 22.9291 0 21.1383 0 18.9293s1.7908-3.9998 3.9998-3.9998 3.9998 1.7908 3.9998 3.9998-1.7908 3.9998-3.9998 3.9998zm19.4643-6.0004L15.4632 3.072C14.3586 1.1587 11.9121.5028 9.9988 1.6074S7.4295 5.1585 8.5341 7.0718l8.0009 13.8567c1.1046 1.9133 3.5511 2.5679 5.4644 1.4646 1.9134-1.1046 2.568-3.5511 1.4647-5.4644zM7.5137 4.8438L1.5645 15.1484A4.5 4.5 0 0 1 4 14.4297c2.5597-.0075 4.6248 2.1585 4.4941 4.7148l3.2168-5.5723-3.6094-6.25c-.4499-.7793-.6322-1.6394-.5878-2.4784z"/></svg>'
    ),
    # Microsoft Ads (four-square logo)
    "microsoft_ads": (
        '<svg viewBox="0 0 24 24" aria-hidden="true">'
        '<path fill="#F25022" d="M1 1h10.4v10.4H1z"/>'
        '<path fill="#7FBA00" d="M12.6 1H23v10.4H12.6z"/>'
        '<path fill="#00A4EF" d="M1 12.6h10.4V23H1z"/>'
        '<path fill="#FFB900" d="M12.6 12.6H23V23H12.6z"/></svg>'
    ),
    # Google Analytics 4
    "ga4": (
        '<svg viewBox="0 0 24 24" fill="#E37400" aria-hidden="true">'
        '<path d="M22.84 2.9982v17.9987c.0086 1.6473-1.3197 2.9897-2.967 2.9984a2.9808 2.9808 0 01-.3677-.0208c-1.528-.226-2.6477-1.5558-2.6105-3.1V3.1204c-.0369-1.5458 1.0856-2.8762 2.6157-3.1 1.6361-.1915 3.1178.9796 3.3093 2.6158.014.1201.0208.241.0202.3619zM4.1326 18.0548c-1.6417 0-2.9726 1.331-2.9726 2.9726C1.16 22.6691 2.4909 24 4.1326 24s2.9726-1.3309 2.9726-2.9726-1.331-2.9726-2.9726-2.9726zm7.8728-9.0098c-.0171 0-.0342 0-.0513.0003-1.6495.0904-2.9293 1.474-2.891 3.1256v7.9846c0 2.167.9535 3.4825 2.3505 3.763 1.6118.3266 3.1832-.7152 3.5098-2.327.04-.1974.06-.3983.0593-.5998v-8.9585c.003-1.6474-1.33-2.9852-2.9773-2.9882z"/></svg>'
    ),
    # Google Search Console
    "gsc": (
        '<svg viewBox="0 0 24 24" fill="#458CF5" aria-hidden="true">'
        '<path d="M8.548 1.156L6.832 2.872v1.682h1.716zm0 3.398v.035H6.832v-.035H3.386L0 7.844v3.577h2.826V8.94c0-.525.429-.954.954-.954h16.476c.525 0 .954.43.954.954v2.48h2.754V7.844l-3.386-3.29H17.3v.035h-1.717v-.035zm7.035 0H17.3V2.872l-1.717-1.716zM8.679 1.188V2.84h6.773V1.188zm11.471 7.07a.834.834 0 00-.132.01l-.543.002c-5.216.014-10.432-.008-15.648.01-.435-.063-.794.436-.716.883v2.264h17.812c-.016-.888.045-1.782-.034-2.666-.104-.342-.427-.502-.739-.502zm-15.422.634a.689.698 0 01.689.698.689.698 0 01-.689.697.689.698 0 01-.688-.697.689.698 0 01.688-.698zm2.134 0a.689.698 0 01.689.698.689.698 0 01-.689.697.689.698 0 01-.688-.697.689.698 0 01.688-.698zM.036 11.645v9.156c0 1.05.858 1.908 1.907 1.908h.883V11.645zm21.174 0v11.064h.882c1.05 0 1.908-.858 1.908-1.908v-9.156zM4.057 13.133v6.85h6.137v-6.85zm13.243.021v3.777l-1.708.977-1.708-.977v-3.758a4.006 4.006 0 000 7.23v2.441h3.457v-2.442a4.006 4.006 0 00-.041-7.248zm-13.243 8.26v1.43h7.925v-1.43z"/></svg>'
    ),
    # Google Tag Manager
    "gtm": (
        '<svg viewBox="0 0 24 24" fill="#246FDB" aria-hidden="true">'
        '<path d="M12.003 0a3 3 0 0 0-2.121 5.121l6.865 6.865-4.446 4.541 1.745 1.836a3.432 3.432 0 0 1 .7.739l.012.011-.001.002a3.432 3.432 0 0 1 .609 1.953 3.432 3.432 0 0 1-.09.78l7.75-7.647c.031-.029.067-.05.098-.08.023-.023.038-.052.06-.076a2.994 2.994 0 0 0-.06-4.166l-9-9A2.99 2.99 0 0 0 12.003 0zM8.63 2.133L.88 9.809a2.998 2.998 0 0 0 0 4.238l7.7 7.75a3.432 3.432 0 0 1-.077-.729 3.432 3.432 0 0 1 3.431-3.431 3.432 3.432 0 0 1 .826.101l-5.523-5.81 4.371-4.373-2.08-2.08c-.903-.904-1.193-2.183-.898-3.342zm3.304 16.004a2.932 2.932 0 0 0-2.931 2.931A2.932 2.932 0 0 0 11.934 24a2.932 2.932 0 0 0 2.932-2.932 2.932 2.932 0 0 0-2.932-2.931z"/></svg>'
    ),
    # HubSpot
    "hubspot": (
        '<svg viewBox="0 0 24 24" fill="#FF7A59" aria-hidden="true">'
        '<path d="M18.164 7.93V5.084a2.198 2.198 0 001.267-1.978v-.067A2.2 2.2 0 0017.238.845h-.067a2.2 2.2 0 00-2.193 2.193v.067a2.196 2.196 0 001.252 1.973l.013.006v2.852a6.22 6.22 0 00-2.969 1.31l.012-.01-7.828-6.095A2.497 2.497 0 104.3 4.656l-.012.006 7.697 5.991a6.176 6.176 0 00-1.038 3.446c0 1.343.425 2.588 1.147 3.607l-.013-.02-2.342 2.343a1.968 1.968 0 00-.58-.095h-.002a2.033 2.033 0 102.033 2.033 1.978 1.978 0 00-.1-.595l.005.014 2.317-2.317a6.247 6.247 0 104.782-11.134l-.036-.005zm-.964 9.378a3.206 3.206 0 113.215-3.207v.002a3.206 3.206 0 01-3.207 3.207z"/></svg>'
    ),
    # Semrush
    "semrush": (
        '<svg viewBox="0 0 24 24" fill="#FF642D" aria-hidden="true">'
        '<path d="M20.698 11.911c0 .444-.226.516-.79.516-.596 0-.706-.1-.77-.554-.118-1.152-.896-2.13-2.201-2.24-.418-.034-.518-.19-.518-.706 0-.48.074-.708.446-.708 2.265.01 3.833 1.832 3.833 3.69v.002zm3.3 0c0-3.456-2.338-7.11-7.74-7.11H5.52c-.218 0-.354.11-.354.31 0 .109.082.209.156.26.388.31.97.654 1.73 1.036.743.372 1.323.616 1.903.852.246.1.336.208.336.344 0 .19-.136.308-.4.308H.372c-.254 0-.372.164-.372.326 0 .136.044.254.162.372.69.726 1.796 1.596 3.4 2.604 1.466.91 2.98 1.74 4.533 2.492.236.11.308.236.308.372-.008.154-.126.28-.4.28H4.1c-.216 0-.344.12-.344.3 0 .1.08.226.19.326.888.808 2.311 1.688 4.207 2.494 2.53 1.08 5.094 1.721 7.98 1.721 5.465 0 7.867-4.087 7.867-7.289l-.002.002zm-7.133 5.104c-2.794 0-5.132-2.276-5.132-5.114 0-2.794 2.33-5.04 5.132-5.04 2.863 0 5.111 2.24 5.111 5.04a5.086 5.086 0 0 1-5.111 5.114z"/></svg>'
    ),
    # Google Business Profile (map pin)
    "google_business": (
        '<svg viewBox="0 0 24 24" fill="#4285F4" aria-hidden="true">'
        '<path d="M12 2a7 7 0 0 0-7 7c0 5.25 7 13 7 13s7-7.75 7-13a7 7 0 0 0-7-7zm0 9.5A2.5 2.5 0 1 1 12 6.5a2.5 2.5 0 0 1 0 5z"/></svg>'
    ),
    # PageSpeed Insights
    "pagespeed": (
        '<svg viewBox="0 0 24 24" fill="#4285F4" aria-hidden="true">'
        '<path d="M22.363 1.636H1.635C.732 1.636 0 2.37.001 3.273L0 20.727v.003c0 .903.733 1.634 1.635 1.634h20.73c.904 0 1.635-.734 1.635-1.637V3.273c.016-.89-.76-1.64-1.637-1.637zM3.979 2.886c.492-.507 1.279.28.77.772-.491.508-1.278-.279-.77-.771zM1.8 2.89c.507-.509 1.28.265.772.771-.493.502-1.274-.28-.772-.771zm21.7 17.838c.012.611-.524 1.148-1.137 1.136H1.635A1.137 1.137 0 0 1 .5 20.727L.501 4.91H23.5v15.819zM11 16.159l5.946-4.577c.235-.2.576.129.389.372l-.002-.002-3.936 6.35a1.638 1.638 0 0 1-2.448.405c-.785-.668-.811-1.835.05-2.548zm4.763-.75c.09-.168 2.002-3.181 2.06-3.35 2.056 1.813 3.029 4.382 2.898 7.026h-3.819c.073-1.39-.29-2.678-1.139-3.676zm-8.679 3.682H3.278c-.357-7.022 7.148-11.735 13.39-7.92l-3.461 2.618c-3.3-.762-6.364 1.71-6.123 5.302z"/></svg>'
    ),
}
_STATUS_LABELS = {
    "not_connected": "Not connected",
    "connected": "Connected",
    "syncing": "Syncing",
    "error": "Error",
    "disconnected": "Disconnected",
    "disabled": "Disabled",
}

_STATUS_CLASSES = {
    "not_connected": "status-neutral",
    "connected": "status-ok",
    "syncing": "status-syncing",
    "error": "status-error",
    "disconnected": "status-neutral",
    "disabled": "status-neutral",
}

_CONNECTOR_CSS = """
  .connectors-page-title { font-size: 1.45rem; font-weight: 700; color: var(--navy); margin: 0 0 6px; }
  .connectors-page-sub { font-size: 0.95rem; color: var(--muted); margin: 0 0 20px; }

  /* Connector name filter */
  .connector-search-wrap { max-width: 340px; margin: 0 0 20px; }
  .connector-search {
    width: 100%; box-sizing: border-box;
    padding: 10px 12px 10px 36px; font: inherit; font-size: 0.9rem; color: var(--navy);
    border: 1px solid var(--border); border-radius: 9px; background: #fff;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='11' cy='11' r='8'/%3E%3Cpath d='M21 21l-4.35-4.35'/%3E%3C/svg%3E");
    background-repeat: no-repeat; background-position: 12px center;
  }
  .connector-search::placeholder { color: #94a3b8; }
  .connector-search:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(37,99,235,.12); }
  .connector-nomatch { font-size: 0.92rem; color: var(--muted); margin: 4px 0 0; }
  .connector-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 16px;
  }
  .connector-card {
    background: #fff;
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 22px 24px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    box-shadow: 0 1px 4px rgba(10,37,64,.04);
    transition: box-shadow .15s, border-color .15s;
  }
  .connector-card:hover { box-shadow: 0 4px 16px rgba(10,37,64,.08); border-color: #b8c4d4; }
  .connector-card[hidden] { display: none; }
  .connector-card-header { display: flex; align-items: center; gap: 14px; }
  .connector-icon {
    width: 44px; height: 44px; border-radius: 10px;
    background: #f0f4f9; display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; color: var(--navy);
  }
  .connector-icon svg { width: 26px; height: 26px; }
  .connector-card-name { font-size: 1rem; font-weight: 650; color: var(--navy); }
  .connector-status-row { display: flex; align-items: center; gap: 8px; min-height: 22px; }
  .status-dot {
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  }
  .status-neutral .status-dot { background: #9ca3af; }
  .status-ok .status-dot { background: #22c55e; }
  .status-syncing .status-dot { background: #3b82f6; animation: pulse-dot 1.2s infinite; }
  .status-error .status-dot { background: #ef4444; }
  @keyframes pulse-dot { 0%,100%{opacity:1} 50%{opacity:.4} }
  .status-label { font-size: 0.85rem; color: var(--muted); }
  .status-ok .status-label { color: #16a34a; font-weight: 600; }
  .status-error .status-label { color: #dc2626; font-weight: 600; }
  .connector-last-sync { font-size: 0.78rem; color: var(--muted); }
  .connector-sync-range { font-size: 0.72rem; color: var(--muted); opacity: 0.85; margin-top: 2px; }
  .connector-card-action { margin-top: auto; }
  .btn-connect {
    display: inline-block; padding: 8px 18px; border-radius: 8px;
    background: var(--accent); color: #fff; font-size: 0.88rem; font-weight: 650;
    text-decoration: none; border: none; cursor: pointer;
    transition: background .15s;
  }
  .btn-connect:hover { background: #1d4ed8; }
  .btn-manage {
    display: inline-block; padding: 8px 18px; border-radius: 8px;
    background: #f0f4f9; color: var(--navy); font-size: 0.88rem; font-weight: 650;
    text-decoration: none; border: 1px solid var(--border); cursor: pointer;
    transition: background .15s;
  }
  .btn-manage:hover { background: #e2e8f0; }

  /* ── Wizard / Management page ── */
  .connector-detail-header { display: flex; align-items: center; gap: 16px; margin-bottom: 28px; }
  .connector-detail-icon {
    width: 56px; height: 56px; border-radius: 13px;
    background: #f0f4f9; display: flex; align-items: center; justify-content: center; color: var(--navy);
  }
  .connector-detail-icon svg { width: 32px; height: 32px; }
  .connector-detail-title { font-size: 1.4rem; font-weight: 700; color: var(--navy); margin: 0 0 4px; }
  .connector-detail-status { display: flex; align-items: center; gap: 8px; }

  /* Stepper */
  .wizard-stepper {
    counter-reset: step;
    display: flex; flex-direction: column; gap: 0;
    border: 1px solid var(--border); border-radius: 14px;
    overflow: hidden; background: #fff;
    max-width: 680px;
  }
  .wizard-step {
    border-bottom: 1px solid var(--border);
    padding: 22px 28px;
  }
  .wizard-step:last-child { border-bottom: 0; }
  .wizard-step-header {
    display: flex; align-items: center; gap: 14px; cursor: default;
  }
  .wizard-step-num {
    counter-increment: step;
    width: 30px; height: 30px; border-radius: 50%;
    background: #e5eaf2; color: var(--navy);
    display: flex; align-items: center; justify-content: center;
    font-size: 0.85rem; font-weight: 700; flex-shrink: 0;
  }
  .wizard-step.step-done .wizard-step-num {
    background: #22c55e; color: #fff;
  }
  .wizard-step.step-active .wizard-step-num {
    background: var(--accent); color: #fff;
  }
  .wizard-step-title { font-size: 0.98rem; font-weight: 650; color: var(--navy); }
  .wizard-step-summary { font-size: 0.85rem; color: var(--muted); margin-top: 2px; }
  .wizard-step-body {
    display: none; margin-top: 18px; padding-top: 18px;
    border-top: 1px solid var(--border);
  }
  .wizard-step.step-active .wizard-step-body { display: block; }

  /* Account list */
  .account-list { display: flex; flex-direction: column; gap: 8px; margin: 12px 0; }
  .account-item {
    display: flex; align-items: center; gap: 12px;
    padding: 12px 16px; border: 1px solid var(--border); border-radius: 9px;
    cursor: pointer; transition: border-color .12s, background .12s;
  }
  .account-item:hover { border-color: var(--accent); background: #f0f7ff; }
  .account-item input[type=radio] { accent-color: var(--accent); flex-shrink: 0; }
  .account-item-name { font-size: 0.92rem; font-weight: 600; }
  .account-item-id { font-size: 0.78rem; color: var(--muted); }
  .account-item.selected { border-color: var(--accent); background: #eff6ff; }
  .account-search {
    width: 100%; box-sizing: border-box; margin: 4px 0 0;
    padding: 9px 12px 9px 34px; font-size: 0.9rem;
    border: 1px solid var(--border); border-radius: 8px; background: #fff;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='11' cy='11' r='8'/%3E%3Cpath d='M21 21l-4.35-4.35'/%3E%3C/svg%3E");
    background-repeat: no-repeat; background-position: 11px center;
  }
  .account-search:focus { outline: none; border-color: var(--accent); }
  .account-nomatch { color: var(--muted); font-size: 0.88rem; padding: 8px 2px 2px; }

  /* Destination fields */
  .dest-field { margin-bottom: 16px; }
  .dest-field label { display: block; font-size: 0.82rem; font-weight: 650; color: var(--navy); margin-bottom: 6px; }
  .dest-field input {
    width: 100%; padding: 9px 12px; border: 1px solid var(--border); border-radius: 8px;
    font: inherit; font-size: 0.92rem; color: var(--navy); background: #fff;
  }
  .dest-field input:focus { outline: 2px solid var(--accent); border-color: transparent; }

  /* Action buttons */
  .wizard-actions { display: flex; gap: 10px; margin-top: 20px; flex-wrap: wrap; }
  .btn-primary {
    padding: 10px 22px; border-radius: 8px; background: var(--accent); color: #fff;
    font: inherit; font-size: 0.92rem; font-weight: 650; border: none; cursor: pointer;
    transition: background .15s;
  }
  .btn-primary:hover { background: #1d4ed8; }
  .btn-primary:disabled { background: #9ca3af; cursor: not-allowed; }
  .btn-secondary {
    padding: 10px 22px; border-radius: 8px; background: #fff; color: var(--navy);
    font: inherit; font-size: 0.92rem; font-weight: 600;
    border: 1px solid var(--border); cursor: pointer; transition: background .15s;
  }
  .btn-secondary:hover { background: #f4f7fb; }

  /* Spinner */
  .spinner {
    display: inline-block; width: 20px; height: 20px; border-radius: 50%;
    border: 2px solid #e5eaf2; border-top-color: var(--accent);
    animation: spin .7s linear infinite; vertical-align: middle; margin-right: 8px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Test result */
  .test-result { padding: 14px 18px; border-radius: 9px; font-size: 0.9rem; margin-top: 14px; }
  .test-result.ok { background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; }
  .test-result.err { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }

  /* Management view */
  .mgmt-section {
    background: #fff; border: 1px solid var(--border); border-radius: 14px;
    padding: 24px 28px; margin-bottom: 16px; max-width: 680px;
  }
  .mgmt-section-title { font-size: 0.92rem; font-weight: 700; color: var(--muted); letter-spacing:.04em; text-transform:uppercase; margin: 0 0 16px; }
  .mgmt-row { display: flex; align-items: baseline; gap: 14px; padding: 6px 0; border-bottom: 1px solid #f1f5f9; }
  .mgmt-row:last-child { border-bottom: 0; }
  .mgmt-label { font-size: 0.85rem; color: var(--muted); min-width: 160px; flex-shrink: 0; }
  .mgmt-value { font-size: 0.92rem; color: var(--navy); font-weight: 500; }
  .mgmt-actions { display: flex; flex-wrap: wrap; gap: 10px; max-width: 680px; margin-top: 8px; }

  /* BigQuery destination strip (directory page, admins only) */
  .bq-dest {
    background: #fff; border: 1px solid var(--border); border-radius: 14px;
    padding: 20px 24px; margin-top: 28px; max-width: 680px;
  }
  .bq-dest-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
  .bq-dest-title { font-size: 0.92rem; font-weight: 700; color: var(--muted); letter-spacing: .04em; text-transform: uppercase; }
  .bq-dest-sub { font-size: 0.85rem; color: var(--muted); margin-top: 5px; line-height: 1.5; max-width: 400px; }
  .bq-dest .btn-secondary { flex-shrink: 0; }
  .bq-pill {
    display: inline-flex; align-items: center; gap: 7px; margin-top: 14px;
    font-size: 0.8rem; font-weight: 600; padding: 4px 11px; border-radius: 999px;
    border: 1px solid #e2e8f0; background: #f7f9fc; color: #6b7a90;
  }
  .bq-pill::before { content: ''; width: 8px; height: 8px; border-radius: 50%; background: #c5cdd9; flex-shrink: 0; }
  .bq-pill[data-state="checking"] { color: #1d6fd0; border-color: #bcd4f0; background: #eef5fd; }
  .bq-pill[data-state="checking"]::before { background: #1d6fd0; }
  .bq-pill[data-state="ok"] { color: #0a7f3f; border-color: #b8dfc8; background: #e9f7ef; }
  .bq-pill[data-state="ok"]::before { background: #0a7f3f; }
  .bq-pill[data-state="err"] { color: #b42318; border-color: #f3c0bb; background: #fdecea; }
  .bq-pill[data-state="err"]::before { background: #b42318; }
  .btn-danger {
    padding: 9px 18px; border-radius: 8px; background: #fff; color: #dc2626;
    font: inherit; font-size: 0.88rem; font-weight: 650;
    border: 1px solid #fca5a5; cursor: pointer; transition: background .15s;
  }
  .btn-danger:hover { background: #fef2f2; }

  /* Sync history table */
  .sync-history-wrap { overflow-x: auto; }
  .sync-history { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  .sync-history th, .sync-history td { white-space: nowrap; }
  .sync-history th {
    text-align: left; padding: 8px 12px; font-size: 0.78rem; font-weight: 700;
    color: var(--muted); letter-spacing:.04em; text-transform:uppercase;
    border-bottom: 1px solid var(--border);
  }
  .sync-history td { padding: 9px 12px; border-bottom: 1px solid #f1f5f9; color: var(--navy); }
  .sync-history tr:last-child td { border-bottom: 0; }
  .run-status-ok { color: #16a34a; font-weight: 600; }
  .run-status-failed { color: #dc2626; font-weight: 600; }
  .run-status-running { color: #3b82f6; }
  .run-status-cancelled { color: #92400e; }
  .run-status-running .run-status-label { margin-right: 8px; }
  .cancel-run-btn { font-size: .72rem; font-weight: 600; padding: 2px 9px; border-radius: 5px; border: 1px solid #dc2626; color: #dc2626; background: #fff; cursor: pointer; vertical-align: middle; }
  .cancel-run-btn:hover { background: #fef2f2; }
  .cancel-run-btn:disabled { opacity: .5; cursor: default; }

  /* Disconnect modal */
  .modal-overlay {
    display: none; position: fixed; inset: 0; background: rgba(8,18,33,.5);
    z-index: 200; align-items: center; justify-content: center;
  }
  .modal-overlay.open { display: flex; }
  .modal-box {
    background: #fff; border-radius: 16px; padding: 32px; max-width: 440px; width: 90%;
    box-shadow: 0 20px 60px rgba(8,18,33,.2);
  }
  .modal-title { font-size: 1.1rem; font-weight: 700; color: var(--navy); margin: 0 0 12px; }
  .modal-body { font-size: 0.92rem; color: var(--muted); line-height: 1.6; margin-bottom: 24px; }
  .modal-actions { display: flex; gap: 10px; justify-content: flex-end; }

  .back-link { display: inline-flex; align-items: center; gap: 6px; color: var(--muted); font-size: 0.88rem; text-decoration: none; margin-bottom: 20px; }
  .back-link:hover { color: var(--navy); }
  .back-link svg { width: 14px; height: 14px; }
"""


# ──────────────────────────────────────────────────────────────────────────────
# Directory page
# ──────────────────────────────────────────────────────────────────────────────

def _bq_destination_html(
    *,
    client_slug: str,
    access_key: str | None,
    session_is_admin: bool,
    bq_project: str | None,
    bq_marts_dataset: str | None,
) -> str:
    """Admin-only strip under the connector grid: where this client's dashboard
    reads from, plus an on-demand access check.

    The destination is *set* per connector (the wizard's step 3, which syncs the
    project onto the client and provisions the datasets on save). This is the
    read-back of the client-level result, with the same provision + access check
    on a button — so "data stopped showing" can be diagnosed as revoked IAM
    without re-saving a connector just to re-trigger the check.
    """
    if not session_is_admin:
        return ""
    verify_url = f"/api/clients/{client_slug}/bq-verify"
    if access_key:
        verify_url = f"{verify_url}?key={_url_quote(access_key, safe='')}"
    return f"""
      <div class="bq-dest" id="bqDestCard">
        <div class="bq-dest-head">
          <div>
            <div class="bq-dest-title">BigQuery destination</div>
            <div class="bq-dest-sub">Where this client's dashboard reads from. Set per connector in each connector's Destination step.</div>
          </div>
          <button type="button" class="btn-secondary bq-verify" data-url="{_esc(verify_url)}">Verify access</button>
        </div>
        <div class="mgmt-row">
          <span class="mgmt-label">GCP project</span>
          <span class="mgmt-value">{_esc(bq_project or '— not set yet')}</span>
        </div>
        <div class="mgmt-row">
          <span class="mgmt-label">Mart dataset</span>
          <span class="mgmt-value">{_esc(bq_marts_dataset or 'marketing_marts')}</span>
        </div>
        <span class="bq-pill" data-state="idle">Not verified yet</span>
      </div>
      <script>{_bq_verify_js()}</script>"""


def _bq_verify_js() -> str:
    """Verify button → POST /api/clients/<slug>/bq-verify, result in the pill."""
    return """
    (function(){
      var card = document.getElementById('bqDestCard');
      if (!card) return;
      var btn = card.querySelector('.bq-verify'), pill = card.querySelector('.bq-pill');
      if (!btn || !pill) return;
      function setPill(state, text){ pill.dataset.state = state; pill.textContent = text; }
      btn.addEventListener('click', async function(){
        btn.disabled = true; setPill('checking', 'Checking\\u2026');
        try {
          var r = await fetch(btn.dataset.url, {method:'POST', credentials:'same-origin'});
          var b = await r.json().catch(function(){ return {}; });
          if (b.ok) setPill('ok', b.message || 'Connected & readable');
          else setPill('err', b.error || 'Verification failed');
        } catch (err) { setPill('err', 'Failed: ' + (err.message || err)); }
        finally { btn.disabled = false; }
      });
    })();
    """


def _connector_search_js() -> str:
    """Filter the connector cards as the user types in the search box."""
    return """
    (function(){
      var input = document.getElementById('connectorSearch');
      var grid = document.getElementById('connectorGrid');
      if (!input || !grid) return;
      var cards = Array.prototype.slice.call(grid.querySelectorAll('.connector-card'));
      var nomatch = document.getElementById('connectorNoMatch');
      var nomatchTerm = nomatch ? nomatch.querySelector('strong') : null;
      function apply(){
        var raw = input.value.trim();
        var q = raw.toLowerCase().replace(/\\s+/g, ' ');
        var shown = 0;
        cards.forEach(function(card){
          var hit = !q || (card.getAttribute('data-search') || '').indexOf(q) !== -1;
          card.hidden = !hit;
          if (hit) shown++;
        });
        if (nomatch) {
          if (nomatchTerm) nomatchTerm.textContent = '\\u201c' + raw + '\\u201d';
          nomatch.hidden = shown > 0;
        }
      }
      input.addEventListener('input', apply);
      input.addEventListener('search', apply);
      apply();
    })();
    """


def render_connectors_directory(
    *,
    client_slug: str,
    label: str,
    configs: list[connector_config_store.ConnectorConfig],
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    flash_message: str | None = None,
    flash_error: str | None = None,
    bq_project: str | None = None,
    bq_marts_dataset: str | None = None,
) -> str:
    by_type = {c.connector_type: c for c in configs}
    handlers = all_handlers()
    cards: list[str] = []

    for ctype in CONNECTOR_ORDER:
        handler = handlers.get(ctype)
        if not handler:
            continue
        cfg = by_type.get(ctype)
        status = cfg.status if cfg else "not_connected"
        status_label = _STATUS_LABELS.get(status, status)
        status_cls = _STATUS_CLASSES.get(status, "status-neutral")
        icon = _PLATFORM_ICONS.get(ctype, "")
        detail_url = f"/dashboard/{client_slug}/connectors/{ctype}"

        last_sync_html = ""
        if cfg and cfg.last_success_at:
            ts = cfg.last_success_at
            rows_suffix = f" · {cfg.last_rows_loaded:,} rows" if cfg.last_rows_loaded is not None else ""
            last_sync_html = f'<div class="connector-last-sync">Last sync: {_fmt_dt(ts)}{rows_suffix}</div>'
        elif cfg and cfg.last_error_message:
            last_sync_html = f'<div class="connector-last-sync" style="color:#dc2626">Error: {_esc(cfg.last_error_message[:80])}</div>'

        if status in ("connected", "syncing", "error", "disabled"):
            action_btn = f'<a href="{_esc(detail_url)}" class="btn-manage">Manage</a>'
        else:
            action_btn = f'<a href="{_esc(detail_url)}" class="btn-connect">Connect</a>'

        # Matched against the search box: display name plus the connector key, so
        # "gsc" finds Search Console and "linkedi" finds both LinkedIn cards.
        search_key = " ".join(
            dict.fromkeys(
                (handler.display_name.lower(), ctype, ctype.replace("_", " "))
            )
        )

        cards.append(f"""
          <div class="connector-card" data-search="{_esc(search_key)}">
            <div class="connector-card-header">
              <div class="connector-icon">{icon}</div>
              <div class="connector-card-name">{_esc(handler.display_name)}</div>
            </div>
            <div class="connector-status-row {_esc(status_cls)}">
              <span class="status-dot"></span>
              <span class="status-label">{_esc(status_label)}</span>
            </div>
            {last_sync_html}
            <div class="connector-card-action">{action_btn}</div>
          </div>
        """)

    flash_html = _flash_html(flash_message, flash_error)
    bq_html = _bq_destination_html(
        client_slug=client_slug,
        access_key=access_key,
        session_is_admin=session_is_admin,
        bq_project=bq_project,
        bq_marts_dataset=bq_marts_dataset,
    )
    content = f"""
      {flash_html}
      <h1 class="connectors-page-title">Data Connectors</h1>
      <p class="connectors-page-sub">Connect your marketing platforms to enable reporting and daily data sync.</p>
      <div class="connector-search-wrap">
        <input type="search" id="connectorSearch" class="connector-search"
               placeholder="Search connectors&hellip;" autocomplete="off" spellcheck="false"
               aria-label="Filter connectors by name" aria-controls="connectorGrid">
      </div>
      <div class="connector-grid" id="connectorGrid">{"".join(cards)}</div>
      <p class="connector-nomatch" id="connectorNoMatch" role="status" hidden>No connectors match <strong></strong>.</p>
      <script>{_connector_search_js()}</script>
      {bq_html}
    """
    return render_client_shell_page(
        client_slug=client_slug,
        label=label,
        active_nav="connectors",
        page_title="Connectors",
        page_subtitle="",
        content_html=content,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        admin_tab="connectors",
        extra_css=_CONNECTOR_CSS,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Connector detail page (wizard or management)
# ──────────────────────────────────────────────────────────────────────────────

def render_connector_detail(
    *,
    client_slug: str,
    label: str,
    connector_type: str,
    handler: ConnectorHandler,
    config: connector_config_store.ConnectorConfig | None,
    sync_runs: list[connector_config_store.ConnectorSyncRun],
    db_config: Any,  # ClientConfigRow | None
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    oauth_done: bool = False,
    oauth_error: str | None = None,
    flash_message: str | None = None,
    flash_error: str | None = None,
    force_wizard: bool = False,
) -> str:
    icon = _PLATFORM_ICONS.get(connector_type, "")
    dir_url = f"/dashboard/{client_slug}/connectors"
    is_connected = config and config.status in ("connected", "syncing", "error", "disabled")

    # "Change account" (?reconfigure=1) forces the setup wizard even for an
    # already-connected/errored connector, so the account can be re-picked or the
    # OAuth re-authorized. Without this, the base URL always re-renders the
    # read-only management view and there's no path back to the wizard.
    if is_connected and not force_wizard:
        content = _render_management_view(
            client_slug=client_slug,
            handler=handler,
            config=config,
            sync_runs=sync_runs,
            icon=icon,
            dir_url=dir_url,
            flash_message=flash_message,
            flash_error=flash_error,
            access_key=access_key,
        )
    else:
        content = _render_wizard(
            client_slug=client_slug,
            handler=handler,
            config=config,
            db_config=db_config,
            icon=icon,
            dir_url=dir_url,
            oauth_done=oauth_done,
            oauth_error=oauth_error,
            flash_message=flash_message,
            flash_error=flash_error,
        )

    return render_client_shell_page(
        client_slug=client_slug,
        label=label,
        active_nav="connectors",
        page_title=handler.display_name,
        page_subtitle="",
        content_html=content,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        admin_tab="connectors",
        extra_css=_CONNECTOR_CSS,
    )


def _render_wizard(
    *,
    client_slug: str,
    handler: ConnectorHandler,
    config: connector_config_store.ConnectorConfig | None,
    db_config: Any,
    icon: str,
    dir_url: str,
    oauth_done: bool,
    oauth_error: str | None,
    flash_message: str | None,
    flash_error: str | None,
) -> str:
    status = config.status if config else "not_connected"
    is_disconnected = status == "disconnected"
    has_oauth = config is not None and config.oauth_client_slug == client_slug

    # no_oauth connectors (GA4, GSC service-account) don't need authorization —
    # treat step 1 as always done.
    if handler.no_oauth:
        has_oauth = True
    else:
        has_oauth = has_oauth or oauth_done

    # Determine starting step
    start_step = 1
    if has_oauth:
        start_step = 2
    if config and config.source_account_id and has_oauth:
        start_step = 3

    oauth_error_html = ""
    if oauth_error:
        oauth_error_html = f'<div class="test-result err" style="margin-bottom:16px">{_esc(oauth_error)}</div>'

    flash_html = _flash_html(flash_message, flash_error)

    # OAuth start URL — return_to must be URL-encoded so ?oauth_done=1 isn't
    # parsed as an extra outer query param by the connect endpoint.
    _return_to = _url_quote(
        f"/dashboard/{client_slug}/connectors/{handler.connector_type}?oauth_done=1",
        safe="",
    )
    oauth_start_url = (
        f"/oauth/{handler.oauth_platform}/connect"
        f"?return_to={_return_to}"
        f"&client={_url_quote(client_slug, safe='')}"
    )

    # Pre-fill destination from existing config or db_config
    bq_project = (config and config.bq_project_id) or (db_config and db_config.gcp_project_id) or ""
    raw_ds = (config and config.raw_dataset_id) or handler.default_raw_dataset
    mart_ds = (config and config.mart_dataset_id) or handler.default_mart_dataset

    reconnect_note = ""
    if is_disconnected:
        reconnect_note = '<p style="color:var(--muted);font-size:.9rem;margin-bottom:20px">This connector was previously set up. Complete the steps below to reconnect.</p>'

    # Step 1 (auth) presentation:
    #  - agency_oauth (GSC): one shared agency Google OAuth (global token), SA fallback
    #  - no_oauth: pure server-side service account
    #  - otherwise: per-client OAuth authorize
    if getattr(handler, "agency_oauth", False):
        import oauth_store as _oauth_store
        try:
            _agency_connected = _oauth_store.public_status(handler.oauth_platform).connected
        except Exception:
            _agency_connected = False
        # No &client= → the connect route stores a GLOBAL token (client_slug=''),
        # which is what _gsc_read_creds reads. Admin-gated.
        _agency_connect_url = f"/oauth/{handler.oauth_platform}/connect?return_to={_return_to}"
        if _agency_connected:
            step1_summary = '<div class="wizard-step-summary">Connected · agency Google account</div>'
            step1_body = (
                '<p style="color:var(--muted);font-size:.9rem">Authorized via the shared agency '
                'Google account — one connection covers every client. '
                f'<a href="{_esc(_agency_connect_url)}">Re-authorize</a></p>'
            )
        else:
            step1_summary = '<div class="wizard-step-summary">Service account (fallback)</div>'
            step1_body = (
                '<p style="color:var(--muted);font-size:.9rem;margin-bottom:16px">Currently using the '
                'server-side service account. Connect the agency Google account to authorize via OAuth '
                'instead — one connection covers every client.</p>'
                f'<a href="{_esc(_agency_connect_url)}" class="btn-connect">Connect agency Google account</a>'
            )
    elif handler.no_oauth:
        step1_summary = '<div class="wizard-step-summary">Service account configured</div>' if has_oauth else ''
        step1_body = '<p style="color:var(--muted);font-size:.9rem">This connector uses a server-side service account — no authorization needed.</p>'
    else:
        step1_summary = (
            '<div class="wizard-step-summary">Authorized · <a href="/dashboard/'
            + client_slug + '/connectors/' + handler.connector_type
            + '/reauth" style="color:var(--muted);font-size:.85em">Re-authorize</a></div>'
        ) if has_oauth else ''
        step1_body = (
            f'<p style="color:var(--muted);font-size:.9rem;margin-bottom:16px">Authorize Sagefrog to '
            f'access your {_esc(handler.display_name)} account.</p>'
            f'<a href="{_esc(oauth_start_url)}" class="btn-connect">Authorize {_esc(handler.display_name)}</a>'
        )
        if handler.connector_type == "hubspot":
            step1_body += (
                '<div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--line)">'
                '<p style="color:var(--muted);font-size:.9rem;margin-bottom:10px">No HubSpot access yourself? '
                'Generate a link to send the specialist or client who manages this portal — they authorize it with '
                'their own login, no account here needed.</p>'
                '<button type="button" class="btn-secondary" onclick="genConnectLink()">Generate connect link</button>'
                '<div id="connectLinkBox" style="display:none;margin-top:10px">'
                '<input id="connectLinkInput" readonly style="width:100%;padding:8px;border:1px solid var(--line);'
                'border-radius:6px;font-size:.8rem;font-family:monospace" onclick="this.select()">'
                '<div id="connectLinkMsg" style="font-size:.8rem;color:var(--muted);margin-top:5px"></div></div>'
                '</div>'
            )
        if handler.connector_type == "linkedin_organic":
            # An organic token missing rw_organization_admin (e.g. minted before
            # this connector moved to its own Community-Management app) can't read
            # follower/page/post analytics. Prompt a reconnect — the OAuth start
            # URL requests the current LINKEDIN_ORGANIC_SCOPES. This token store is
            # separate from LinkedIn Ads, so Ads is unaffected either way.
            try:
                from connectors.linkedin_organic import needs_scope_reauth as _needs_reauth
                _reauth = _needs_reauth(client_slug)
            except Exception:
                _reauth = False
            if _reauth:
                step1_body += (
                    '<div style="margin-top:16px;padding:12px 14px;border:1px solid #e0a800;'
                    'border-radius:8px;background:rgba(224,168,0,.08)">'
                    '<p style="font-size:.9rem;margin:0 0 10px">This LinkedIn Organic connection is '
                    'missing organic access (followers, page &amp; post analytics). '
                    'Reconnect to grant the <code>rw_organization_admin</code> scope — your LinkedIn Ads '
                    'connection is unaffected.</p>'
                    f'<a href="{_esc(oauth_start_url)}" class="btn-connect">Reconnect LinkedIn for organic</a>'
                    '</div>'
                )

    steps_html = f"""
    {reconnect_note}
    <div class="wizard-stepper" id="wizardStepper">

      <div class="wizard-step {'step-done' if has_oauth else ('step-active' if start_step == 1 else '')}" id="wizStep1">
        <div class="wizard-step-header">
          <div class="wizard-step-num">{'✓' if has_oauth else '1'}</div>
          <div>
            <div class="wizard-step-title">Connect {_esc(handler.display_name)}</div>
            {step1_summary}
          </div>
        </div>
        <div class="wizard-step-body">
          {oauth_error_html}
          {step1_body}
        </div>
      </div>

      <div class="wizard-step {'step-active' if start_step == 2 and not (config and config.source_account_id) else ('step-done' if config and config.source_account_id else '')}" id="wizStep2">
        <div class="wizard-step-header">
          <div class="wizard-step-num">{'✓' if config and config.source_account_id else '2'}</div>
          <div>
            <div class="wizard-step-title">{'Select account' if not handler.manual_account_entry else handler.manual_account_label}</div>
            {'<div class="wizard-step-summary">' + _esc(config.source_account_name or config.source_account_id or '') + '</div>' if config and config.source_account_id else ''}
          </div>
        </div>
        <div class="wizard-step-body">
          {f'''
          <div class="dest-field">
            <label for="manualAccountInput">{_esc(handler.manual_account_label)}</label>
            <input id="manualAccountInput" type="text" value="{_esc((config.source_account_id if config else '') or '')}" placeholder="example.com" />
          </div>
          <div id="accountError" class="test-result err" style="display:none"></div>
          <div class="wizard-actions">
            <button class="btn-primary" onclick="confirmManualAccount()">Continue</button>
          </div>
          ''' if handler.manual_account_entry else '''
          <div id="accountLoading" style="color:var(--muted);font-size:.9rem">
            <span class="spinner"></span>Loading accounts…
          </div>
          <input id="accountSearch" class="account-search" type="text" placeholder="Search accounts by name or ID…" oninput="filterAccounts()" autocomplete="off" style="display:none" />
          <div id="accountList" class="account-list" style="display:none"></div>
          <div id="accountNoMatch" class="account-nomatch" style="display:none">No accounts match your search.</div>
          <div id="accountError" class="test-result err" style="display:none"></div>
          <div class="wizard-actions" style="display:none" id="step2Actions">
            <button class="btn-primary" id="step2Next" onclick="confirmAccount()">Continue</button>
          </div>
          '''}
        </div>
      </div>

      <div class="wizard-step {'step-active' if start_step >= 3 and not (config and config.bq_project_id) else ''}" id="wizStep3">
        <div class="wizard-step-header">
          <div class="wizard-step-num">3</div>
          <div><div class="wizard-step-title">Confirm destination</div></div>
        </div>
        <div class="wizard-step-body">
          <p style="color:var(--muted);font-size:.9rem;margin-bottom:10px">Where should synced data be written in BigQuery?</p>
          <p style="color:var(--muted);font-size:.82rem;margin-bottom:18px;line-height:1.5">The GCP project must already exist, with <code>marketing-data-reader@sagefrog.iam.gserviceaccount.com</code> granted both <strong>BigQuery Data Editor</strong> and <strong>BigQuery Job User</strong> roles. Continuing creates the datasets and verifies access.</p>
          <div class="dest-field">
            <label for="bqProject">GCP project ID</label>
            <input id="bqProject" type="text" value="{_esc(bq_project)}" placeholder="your-gcp-project" />
          </div>
          <div class="dest-field">
            <label for="rawDataset">Raw dataset</label>
            <input id="rawDataset" type="text" value="{_esc(raw_ds)}" placeholder="{_esc(handler.default_raw_dataset)}" />
          </div>
          <div class="dest-field">
            <label for="martDataset">Mart dataset</label>
            <input id="martDataset" type="text" value="{_esc(mart_ds)}" placeholder="marketing_marts" />
          </div>
          <div class="wizard-actions">
            <button class="btn-primary" id="destContinueBtn" onclick="confirmDestination()">Continue</button>
          </div>
        </div>
      </div>

      <div class="wizard-step" id="wizStep4">
        <div class="wizard-step-header">
          <div class="wizard-step-num">4</div>
          <div><div class="wizard-step-title">Test connection</div></div>
        </div>
        <div class="wizard-step-body">
          <p style="color:var(--muted);font-size:.9rem;margin-bottom:16px">Verify the connection and run a quick data check before enabling daily sync.</p>
          <div class="wizard-actions">
            <button class="btn-primary" id="testBtn" onclick="runTest()">Run connection test</button>
          </div>
          <div id="testStatus" style="display:none"></div>
          <div class="wizard-actions" style="display:none" id="step4Next">
            <button class="btn-primary" onclick="activateStep(5)">Continue</button>
          </div>
        </div>
      </div>

      <div class="wizard-step" id="wizStep5">
        <div class="wizard-step-header">
          <div class="wizard-step-num">5</div>
          <div><div class="wizard-step-title">Enable daily sync</div></div>
        </div>
        <div class="wizard-step-body">
          <p style="color:var(--muted);font-size:.9rem;margin-bottom:16px">
            Your connection is set up. Enable daily sync to keep reporting data fresh.
          </p>
          <label style="display:flex;align-items:center;gap:10px;font-size:.92rem;font-weight:600;cursor:pointer">
            <input type="checkbox" id="syncToggle" checked style="accent-color:var(--accent);width:18px;height:18px">
            Schedule daily sync
          </label>
          <div class="wizard-actions" style="margin-top:20px">
            <button class="btn-primary" onclick="finishSetup()">Finish setup</button>
          </div>
        </div>
      </div>

    </div>
    """

    script = f"""
    <script>
    var _clientSlug = {_js_str(client_slug)};
    var _connType = {_js_str(handler.connector_type)};
    var _manualAccountEntry = {'true' if handler.manual_account_entry else 'false'};
    var _selectedAccountId = null;
    var _selectedAccountName = null;
    var _accountsLoading = false;
    var _accountsLoaded = false;
    var _configuredBqProject = null;
    var _configuredRawDs = null;
    var _configuredMartDs = null;

    function genConnectLink() {{
      var box = document.getElementById('connectLinkBox');
      var inp = document.getElementById('connectLinkInput');
      var msg = document.getElementById('connectLinkMsg');
      if (box) box.style.display = 'block';
      if (msg) msg.textContent = 'Generating…';
      fetch('/dashboard/' + _clientSlug + '/connectors/' + _connType + '/connect-link', {{
        method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: '{{}}'
      }}).then(r => r.json()).then(d => {{
        if (d.ok) {{
          if (inp) {{ inp.value = d.link; inp.select(); }}
          if (msg) msg.textContent = 'Expires in ' + (d.expires_days || 7) + ' days. Send it to whoever can sign into this client\\'s HubSpot.';
        }} else if (msg) {{ msg.textContent = d.error || 'Could not generate link.'; }}
      }}).catch(e => {{ if (msg) msg.textContent = 'Error: ' + e.message; }});
    }}

    function activateStep(n) {{
      document.querySelectorAll('.wizard-step').forEach((el, i) => {{
        var step = i + 1;
        if (step < n) {{
          el.classList.remove('step-active');
          el.classList.add('step-done');
          var num = el.querySelector('.wizard-step-num');
          if (num) num.textContent = '✓';
        }} else if (step === n) {{
          el.classList.add('step-active');
          el.classList.remove('step-done');
        }} else {{
          el.classList.remove('step-active','step-done');
        }}
      }});
      var target = document.getElementById('wizStep' + n);
      if (target) target.scrollIntoView({{behavior:'smooth', block:'nearest'}});
      if (n === 2 && !_manualAccountEntry) loadAccounts();
    }}

    // Allow clicking a completed step header to go back and change the selection
    document.addEventListener('DOMContentLoaded', function() {{
      document.querySelectorAll('.wizard-step').forEach(function(stepEl, i) {{
        var stepNum = i + 1;
        var header = stepEl.querySelector('.wizard-step-header');
        if (!header) return;
        header.style.cursor = 'pointer';
        header.addEventListener('click', function() {{
          if (stepEl.classList.contains('step-done')) {{
            activateStep(stepNum);
          }}
        }});
      }});
    }});

    function loadAccounts() {{
      // Revisiting step 2 (or a double-fire from activateStep) must not launch a
      // second fan-out — that multiplies the burst GTM rate-limits on. Fetch at
      // most once; a failure clears the flag so the user can retry.
      if (_accountsLoading || _accountsLoaded) return;
      _accountsLoading = true;
      var url = '/dashboard/' + _clientSlug + '/connectors/' + _connType + '/accounts';
      fetch(url).then(r => r.json()).then(data => {{
        _accountsLoading = false;
        document.getElementById('accountLoading').style.display = 'none';
        if (!data.ok) {{
          var err = document.getElementById('accountError');
          err.textContent = data.error || 'Failed to load accounts.';
          err.style.display = 'block';
          return;
        }}
        var list = document.getElementById('accountList');
        list.innerHTML = '';
        (data.accounts || []).forEach(acc => {{
          var item = document.createElement('label');
          item.className = 'account-item';
          item.dataset.search = ((acc.name||'') + ' ' + acc.id).toLowerCase();
          item.innerHTML = '<input type="radio" name="account" value="' + acc.id + '" data-name="' + (acc.name||'') + '"> <div><div class="account-item-name">' + (acc.name||acc.id) + '</div><div class="account-item-id">ID: ' + acc.id + '</div></div>';
          item.querySelector('input').addEventListener('change', function() {{
            document.querySelectorAll('.account-item').forEach(i => i.classList.remove('selected'));
            item.classList.add('selected');
            _selectedAccountId = this.value;
            _selectedAccountName = this.dataset.name;
            document.getElementById('step2Actions').style.display = 'flex';
          }});
          list.appendChild(item);
        }});
        list.style.display = 'flex';
        // Reveal the filter only when the list is long enough to warrant it.
        var search = document.getElementById('accountSearch');
        if (search && (data.accounts || []).length > 8) {{
          search.style.display = 'block';
        }}
        if (!data.accounts || !data.accounts.length) {{
          list.innerHTML = '<p style="color:var(--muted);font-size:.9rem">No accounts found for this connection.</p>';
        }}
        _accountsLoaded = true;  // don't re-fan-out on revisits once it succeeds
      }}).catch(err => {{
        _accountsLoading = false;  // allow a retry after a network failure
        document.getElementById('accountLoading').style.display = 'none';
        var errEl = document.getElementById('accountError');
        errEl.textContent = 'Failed to load accounts: ' + err.message;
        errEl.style.display = 'block';
      }});
    }}

    function filterAccounts() {{
      var q = (document.getElementById('accountSearch').value || '').trim().toLowerCase();
      var items = document.querySelectorAll('#accountList .account-item');
      var shown = 0;
      items.forEach(function(it) {{
        var match = !q || (it.dataset.search || '').indexOf(q) !== -1;
        it.style.display = match ? '' : 'none';
        if (match) shown++;
      }});
      var nm = document.getElementById('accountNoMatch');
      if (nm) nm.style.display = (q && shown === 0) ? 'block' : 'none';
    }}

    function confirmAccount() {{
      if (!_selectedAccountId) return;
      var url = '/dashboard/' + _clientSlug + '/connectors/' + _connType + '/configure';
      fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{source_account_id: _selectedAccountId, source_account_name: _selectedAccountName}})
      }}).then(r => r.json()).then(data => {{
        if (data.ok) activateStep(3);
        else alert(data.error || 'Failed to save account selection.');
      }});
    }}

    function confirmManualAccount() {{
      var input = document.getElementById('manualAccountInput');
      var value = (input.value || '').trim().replace(/^https?:\\/\\//, '').replace(/^www\\./, '').replace(/\\/$/, '');
      var errEl = document.getElementById('accountError');
      if (!value) {{
        errEl.textContent = 'Enter a value first.';
        errEl.style.display = 'block';
        return;
      }}
      errEl.style.display = 'none';
      var url = '/dashboard/' + _clientSlug + '/connectors/' + _connType + '/configure';
      fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{source_account_id: value, source_account_name: value}})
      }}).then(r => r.json()).then(data => {{
        if (data.ok) activateStep(3);
        else {{ errEl.textContent = data.error || 'Failed to save.'; errEl.style.display = 'block'; }}
      }});
    }}

    function confirmDestination() {{
      var proj = document.getElementById('bqProject').value.trim();
      var raw = document.getElementById('rawDataset').value.trim();
      var mart = document.getElementById('martDataset').value.trim();
      if (!proj) {{ alert('GCP project ID is required.'); return; }}
      _configuredBqProject = proj; _configuredRawDs = raw; _configuredMartDs = mart;
      // Saving here also provisions + verifies the BigQuery datasets server-side,
      // which does a few BigQuery calls -- show a loading state so it doesn't look
      // frozen, and re-enable on error so the user can fix GCP and retry.
      var btn = document.getElementById('destContinueBtn');
      var _label = btn ? btn.innerHTML : 'Continue';
      if (btn) {{ btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>Setting up BigQuery…'; }}
      var url = '/dashboard/' + _clientSlug + '/connectors/' + _connType + '/configure';
      fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{bq_project_id: proj, raw_dataset_id: raw, mart_dataset_id: mart}})
      }}).then(r => r.json()).then(data => {{
        if (data.ok) {{ activateStep(4); }}
        else {{ if (btn) {{ btn.disabled = false; btn.innerHTML = _label; }} alert(data.error || 'Failed to save destination.'); }}
      }}).catch(err => {{
        if (btn) {{ btn.disabled = false; btn.innerHTML = _label; }}
        alert('Failed to save destination: ' + err.message);
      }});
    }}

    function runTest() {{
      var btn = document.getElementById('testBtn');
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span>Testing…';
      var statusEl = document.getElementById('testStatus');
      statusEl.style.display = 'none';
      var url = '/dashboard/' + _clientSlug + '/connectors/' + _connType + '/test';
      fetch(url, {{method:'POST'}}).then(r => r.json()).then(data => {{
        btn.disabled = false; btn.textContent = 'Run connection test';
        statusEl.className = 'test-result ' + (data.ok ? 'ok' : 'err');
        statusEl.textContent = data.message || (data.ok ? 'Connection verified.' : (data.error || 'Test failed.'));
        statusEl.style.display = 'block';
        if (data.ok) document.getElementById('step4Next').style.display = 'flex';
      }}).catch(err => {{
        btn.disabled = false; btn.textContent = 'Run connection test';
        statusEl.className = 'test-result err';
        statusEl.textContent = 'Test failed: ' + err.message;
        statusEl.style.display = 'block';
      }});
    }}

    function finishSetup() {{
      var syncEnabled = document.getElementById('syncToggle').checked;
      var url = '/dashboard/' + _clientSlug + '/connectors/' + _connType + '/configure';
      fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{sync_enabled: syncEnabled, status: 'connected'}})
      }}).then(() => {{
        window.location.href = '/dashboard/' + _clientSlug + '/connectors/' + _connType + '?connected=1';
      }});
    }}

    // On load: activate the right step
    (function() {{
      var startStep = {start_step};
      activateStep(startStep);
      if (startStep === 2 && !_manualAccountEntry) loadAccounts();
    }})();
    </script>
    """

    return f"""
      <a href="{_esc(dir_url)}" class="back-link">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M19 12H5M12 5l-7 7 7 7"/></svg>
        All connectors
      </a>
      <div class="connector-detail-header">
        <div class="connector-detail-icon">{icon}</div>
        <div>
          <h1 class="connector-detail-title">{_esc(handler.display_name)}</h1>
          {'<p style="color:var(--muted);font-size:.9rem;margin:0">Previously disconnected</p>' if is_disconnected else ''}
        </div>
      </div>
      {flash_html}
      {_flash_html(None, oauth_error)}
      {steps_html}
      {script}
    """


def _render_management_view(
    *,
    client_slug: str,
    handler: ConnectorHandler,
    config: connector_config_store.ConnectorConfig,
    sync_runs: list[connector_config_store.ConnectorSyncRun],
    icon: str,
    dir_url: str,
    flash_message: str | None,
    flash_error: str | None,
    access_key: str | None = None,
) -> str:
    status = config.status
    status_label = _STATUS_LABELS.get(status, status)
    status_cls = _STATUS_CLASSES.get(status, "status-neutral")

    last_sync_html = "Never"
    if config.last_success_at:
        last_sync_html = _fmt_dt(config.last_success_at)

    # HubSpot-specific pull settings: which objects to sync, lifecycle stage and
    # backfill window.
    hubspot_config_html = ""
    if handler.connector_type == "hubspot":
        _hs_stage, _hs_lookback = "marketingqualifiedlead", 90
        _hs_objects = {"contacts": True, "deals": True, "emails": True}
        _stage_opts = [{"value": "marketingqualifiedlead", "label": "Marketing Qualified Lead (MQL)"}]
        _obj_opts = [{"value": k, "label": k.title(), "help": ""} for k in _hs_objects]
        try:
            import hubspot_sync_service as _hs
            _saved = _hs.parse_sync_options(config.sync_options)
            _hs_stage = _saved["lifecycle_stage"]
            _hs_lookback = _saved["lookback_days"]
            _hs_objects = _saved["sync_objects"]
            _stage_opts = _hs.lifecycle_options()
            _obj_opts = _hs.object_options()
        except Exception:
            pass
        _opts_html = "".join(
            f'<option value="{_esc(o["value"])}"{" selected" if o["value"] == _hs_stage else ""}>{_esc(o["label"])}</option>'
            for o in _stage_opts
        )
        _obj_html = "".join(
            f'<label style="display:flex;gap:8px;align-items:flex-start;cursor:pointer">'
            f'<input type="checkbox" class="hs-obj" data-obj="{_esc(o["value"])}"'
            f'{" checked" if _hs_objects.get(o["value"]) else ""} onchange="hubspotObjectsChanged()"'
            f' style="margin-top:3px;cursor:pointer">'
            f'<span><strong style="font-weight:600">{_esc(o["label"])}</strong>'
            f'<span style="display:block;font-size:.78rem;color:var(--muted)">{_esc(o.get("help") or "")}</span>'
            f'</span></label>'
            for o in _obj_opts
        )
        _fld_css = "padding:7px 10px;border:1px solid var(--border);border-radius:6px;font:inherit;font-size:.875rem;background:#fff;color:var(--navy);cursor:pointer"
        hubspot_config_html = f"""
      <div class="mgmt-section">
        <div class="mgmt-section-title">HubSpot pull settings</div>
        <div class="mgmt-row" style="align-items:flex-start">
          <span class="mgmt-label">Data to sync</span>
          <div style="display:flex;flex-direction:column;gap:10px">{_obj_html}</div>
        </div>
        <div class="mgmt-row" style="font-size:.8rem;color:var(--muted)">
          <span class="mgmt-label"></span>
          <span>Only the selected data is pulled into BigQuery — leave one off to keep it
          out of this client's storage. Existing rows are left untouched.</span>
        </div>
        <div class="mgmt-row">
          <span class="mgmt-label">Lifecycle stage</span>
          <select id="hsStage" style="{_fld_css}">{_opts_html}</select>
        </div>
        <div class="mgmt-row">
          <span class="mgmt-label">Backfill window (days)</span>
          <input id="hsLookback" type="number" min="1" max="3650" value="{int(_hs_lookback)}" style="{_fld_css};width:110px">
        </div>
        <div class="mgmt-row" style="font-size:.8rem;color:var(--muted)">
          <span class="mgmt-label"></span>
          <span>Pulls contacts that <em>reached</em> the selected stage within the window — i.e. is or has been that stage.
          The stage applies to contacts; the window applies to contacts and deals. Marketing emails always sync in full.</span>
        </div>
        <div style="margin-top:10px">
          <button class="btn-secondary" id="hsSaveBtn" onclick="saveHubspotOptions()">Save settings</button>
          <span id="hsSaveStatus" style="margin-left:10px;font-size:.85rem"></span>
        </div>
      </div>
"""

    error_row = ""
    if config.last_error_message:
        error_row = f'<div class="test-result err" style="margin-bottom:16px">{_esc(config.last_error_message[:300])}</div>'

    runs_rows = ""
    for run in sync_runs:
        ts = _fmt_dt(run.started_at)
        dur = ""
        if run.completed_at and run.started_at:
            secs = int((run.completed_at - run.started_at).total_seconds())
            dur = f"{secs}s"
        rows_col = str(run.rows_loaded) if run.rows_loaded is not None else "—"
        status_map = {
            "completed":  "run-status-ok",
            "failed":     "run-status-failed",
            "running":    "run-status-running",
            "cancelled":  "run-status-cancelled",
        }
        status_cls_run = status_map.get(run.status, "")
        err_col = _esc(run.error_message[:60]) if run.error_message else ""
        if run.status == "running":
            from urllib.parse import quote as _q
            cancel_url = f"/dashboard/{client_slug}/connectors/{handler.connector_type}/sync/{run.id}/cancel"
            if access_key:
                cancel_url += f"?key={_q(access_key, safe='')}"
            status_cell = (
                f"<td class='{status_cls_run}'>"
                f"<span class='run-status-label'>{_esc(run.status)}</span>"
                f"<button class='cancel-run-btn' data-url='{_esc(cancel_url)}' onclick='cancelRun(this)'>Cancel</button>"
                f"</td>"
            )
        else:
            status_cell = f"<td class='{status_cls_run}'>{_esc(run.status)}</td>"
        runs_rows += f"<tr><td>{_esc(ts)}</td><td>{_esc(run.run_type)}</td>{status_cell}<td>{rows_col}</td><td>{dur}</td><td style='color:#dc2626;font-size:.78rem'>{err_col}</td></tr>"

    runs_section = ""
    if sync_runs:
        runs_section = f"""
        <div class="mgmt-section">
          <div class="mgmt-section-title">Sync history</div>
          <div class="sync-history-wrap">
          <table class="sync-history">
            <thead><tr><th>Started</th><th>Type</th><th>Status</th><th>Rows</th><th>Duration</th><th>Error</th></tr></thead>
            <tbody>{runs_rows}</tbody>
          </table>
          </div>
        </div>
        """

    disconnect_form = f"""
      <form id="disconnectForm" method="POST" action="/dashboard/{client_slug}/connectors/{handler.connector_type}/disconnect" style="display:none"></form>
    """

    return f"""
      <a href="{_esc(dir_url)}" class="back-link">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M19 12H5M12 5l-7 7 7 7"/></svg>
        All connectors
      </a>
      <div class="connector-detail-header">
        <div class="connector-detail-icon">{icon}</div>
        <div>
          <h1 class="connector-detail-title">{_esc(handler.display_name)}</h1>
          <div class="connector-detail-status {_esc(status_cls)}">
            <span class="status-dot"></span>
            <span class="status-label">{_esc(status_label)}</span>
          </div>
        </div>
      </div>
      {_flash_html(flash_message, flash_error)}
      {error_row}

      <div class="mgmt-section">
        <div class="mgmt-section-title">Connection</div>
        <div class="mgmt-row">
          <span class="mgmt-label">Account</span>
          <span class="mgmt-value">{_esc(config.source_account_name or config.source_account_id or '—')}</span>
        </div>
        <div class="mgmt-row">
          <span class="mgmt-label">Account ID</span>
          <span class="mgmt-value">{_esc(config.source_account_id or '—')}</span>
        </div>
      </div>

      <div class="mgmt-section">
        <div class="mgmt-section-title">Destination</div>
        <div class="mgmt-row">
          <span class="mgmt-label">GCP project</span>
          <span class="mgmt-value">{_esc(config.bq_project_id or '—')}</span>
        </div>
        <div class="mgmt-row">
          <span class="mgmt-label">Raw dataset</span>
          <span class="mgmt-value">{_esc(config.raw_dataset_id or '—')}</span>
        </div>
        <div class="mgmt-row">
          <span class="mgmt-label">Mart dataset</span>
          <span class="mgmt-value">{_esc(config.mart_dataset_id or '—')}</span>
        </div>
      </div>

      <div class="mgmt-section">
        <div class="mgmt-section-title">Sync</div>
        <div class="mgmt-row">
          <span class="mgmt-label">Frequency</span>
          <span class="mgmt-value">{_esc(config.sync_frequency.capitalize() if config.sync_frequency else 'Daily')}</span>
        </div>
        <div class="mgmt-row">
          <span class="mgmt-label">Last successful sync</span>
          <span class="mgmt-value">{_esc(last_sync_html)}</span>
        </div>
        <div class="mgmt-row">
          <span class="mgmt-label">Rows loaded</span>
          <span class="mgmt-value">{f"{config.last_rows_loaded:,}" if config.last_rows_loaded is not None else "—"}</span>
        </div>
        <div class="mgmt-row">
          <span class="mgmt-label">Auto-sync enabled</span>
          <span class="mgmt-value">{'Yes' if config.sync_enabled else 'No'}</span>
        </div>
      </div>

      {hubspot_config_html}

      {runs_section}

      <div class="mgmt-actions" style="align-items:center;flex-wrap:wrap;gap:8px">
        <select id="syncDateRange" style="padding:7px 10px;border:1px solid var(--border);border-radius:6px;font:inherit;font-size:.875rem;background:#fff;color:var(--navy);cursor:pointer" title="Date range for this sync run">
          <option value="LAST_5_DAYS">Last 5 days</option>
          <option value="LAST_30_DAYS">Last 30 days</option>
          <option value="LAST_90_DAYS">Last 90 days</option>
          <option value="LAST_180_DAYS">Last 180 days</option>
          <option value="LAST_365_DAYS">Last 365 days (full backfill)</option>
        </select>
        <button class="btn-secondary" id="syncNowBtn" onclick="runSyncNow()">Run sync now</button>
        {'<a href="/dashboard/' + client_slug + '/connectors/' + handler.connector_type + '/reauth" class="btn-secondary">Re-authorize</a>' if not handler.no_oauth else ''}
        <a href="/dashboard/{client_slug}/connectors/{handler.connector_type}?reconfigure=1" class="btn-secondary">Change account</a>
        <button class="btn-danger" onclick="showDisconnectModal()">Disconnect</button>
      </div>

      {disconnect_form}

      <div class="modal-overlay" id="disconnectModal">
        <div class="modal-box">
          <div class="modal-title">Disconnect {_esc(handler.display_name)}?</div>
          <div class="modal-body">
            This will stop future {_esc(handler.display_name)} syncs and remove saved authorization.
            Historical data already in reporting will remain available.
          </div>
          <div class="modal-actions">
            <button class="btn-secondary" onclick="hideDisconnectModal()">Cancel</button>
            <button class="btn-danger" onclick="document.getElementById('disconnectForm').submit()">Disconnect</button>
          </div>
        </div>
      </div>

      <div id="syncNowStatus" style="display:none;margin-top:12px"></div>

      <script>
      function showDisconnectModal() {{ document.getElementById('disconnectModal').classList.add('open'); }}
      function hideDisconnectModal() {{ document.getElementById('disconnectModal').classList.remove('open'); }}
      document.getElementById('disconnectModal').addEventListener('click', function(e) {{
        if (e.target === this) hideDisconnectModal();
      }});

      function hubspotObjects() {{
        var picked = {{}};
        document.querySelectorAll('.hs-obj').forEach(function(el) {{
          picked[el.getAttribute('data-obj')] = el.checked;
        }});
        return picked;
      }}

      // The lifecycle stage only shapes the contacts pull, and the backfill window
      // shapes contacts + deals — grey them out when neither is selected so the
      // fields never look like they're doing something they aren't.
      function hubspotObjectsChanged() {{
        var picked = hubspotObjects();
        var stageEl = document.getElementById('hsStage');
        var lookbackEl = document.getElementById('hsLookback');
        if (stageEl) {{
          stageEl.disabled = !picked.contacts;
          stageEl.style.opacity = picked.contacts ? '1' : '.5';
        }}
        if (lookbackEl) {{
          var windowed = picked.contacts || picked.deals;
          lookbackEl.disabled = !windowed;
          lookbackEl.style.opacity = windowed ? '1' : '.5';
        }}
      }}

      function saveHubspotOptions() {{
        var btn = document.getElementById('hsSaveBtn');
        var st = document.getElementById('hsSaveStatus');
        var stage = (document.getElementById('hsStage') || {{}}).value || 'marketingqualifiedlead';
        var lookback = parseInt((document.getElementById('hsLookback') || {{}}).value || '90', 10) || 90;
        var objects = hubspotObjects();
        if (!Object.keys(objects).some(function(k) {{ return objects[k]; }})) {{
          st.textContent = 'Select at least one type of HubSpot data to sync.';
          return;
        }}
        btn.disabled = true; st.textContent = 'Saving…';
        fetch('/dashboard/{client_slug}/connectors/{handler.connector_type}/sync-options', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{lifecycle_stage: stage, lookback_days: lookback, sync_objects: objects}})
        }})
          .then(r => r.json()).then(data => {{
            btn.disabled = false;
            st.textContent = data.ok ? 'Saved.' : (data.error || 'Save failed.');
          }}).catch(err => {{ btn.disabled = false; st.textContent = 'Save failed: ' + err.message; }});
      }}

      if (document.querySelector('.hs-obj')) hubspotObjectsChanged();

      function runSyncNow() {{
        var btn = document.getElementById('syncNowBtn');
        var statusEl = document.getElementById('syncNowStatus');
        var dateRange = (document.getElementById('syncDateRange') || {{}}).value || 'LAST_5_DAYS';
        btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>Syncing…';
        statusEl.style.display = 'none';
        fetch('/dashboard/{client_slug}/connectors/{handler.connector_type}/sync', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{date_range: dateRange}})
        }})
          .then(r => r.json()).then(data => {{
            btn.disabled = false; btn.textContent = 'Run sync now';
            statusEl.className = 'test-result ' + (data.ok ? 'ok' : 'err');
            statusEl.textContent = data.message || (data.ok ? 'Sync started.' : (data.error || 'Sync failed.'));
            statusEl.style.display = 'block';
            if (data.ok) setTimeout(() => location.reload(), 1500);
          }}).catch(err => {{
            btn.disabled = false; btn.textContent = 'Run sync now';
            statusEl.className = 'test-result err';
            statusEl.textContent = 'Sync failed: ' + err.message;
            statusEl.style.display = 'block';
          }});
      }}

      async function cancelRun(btn) {{
        btn.disabled = true;
        btn.textContent = 'Cancelling…';
        try {{
          const resp = await fetch(btn.dataset.url, {{method: 'POST'}});
          const data = await resp.json();
          if (data.ok) {{
            const td = btn.closest('td');
            td.className = 'run-status-cancelled';
            td.textContent = 'cancelled';
          }} else {{
            btn.disabled = false;
            btn.textContent = 'Cancel';
            alert('Cancel failed: ' + (data.error || 'unknown error'));
          }}
        }} catch(e) {{
          btn.disabled = false;
          btn.textContent = 'Cancel';
        }}
      }}
      </script>
    """


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _flash_html(message: str | None, error: str | None) -> str:
    if error:
        return f'<div class="test-result err" style="margin-bottom:16px">{_esc(str(error)[:300])}</div>'
    if message:
        return f'<div class="test-result ok" style="margin-bottom:16px">{_esc(str(message)[:300])}</div>'
    return ""


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "—"
    now = datetime.now(tz=UTC)
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        diff = now - dt
        secs = int(diff.total_seconds())
        if secs < 60:
            return "Just now"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return dt.strftime("%b %d at %I:%M %p").replace(" 0", " ").replace(":0", ":")
    except Exception:
        return str(dt)[:16]


def _js_str(value: str) -> str:
    import json
    return json.dumps(str(value))
