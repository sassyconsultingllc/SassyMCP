# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-HWSVXZ4R2LYC
"""LemonSqueezy post-dashboard setup automation for SassyMCP.

The LS API does NOT support creating stores / products / variants — those
are dashboard-only. Once those exist, this script handles everything
else via the API:

  list      — inventory: store(s), products, variants with their IDs
  variants  — emit the DEFAULT_VARIANT_MAP block ready to paste into
              sassymcp/_lemonsqueezy.py based on product/variant names
  webhook   — create (or update) the LS → SassyMCP billing Worker webhook
              subscription and print the signing secret

Usage:

  setx LEMONSQUEEZY_API_KEY "eyJ..."          (or set in current shell)
  python tools/lemonsqueezy_setup.py list
  python tools/lemonsqueezy_setup.py variants
  python tools/lemonsqueezy_setup.py webhook \\
      --url https://billing.sassyconsultingllc.com/lemonsqueezy/webhook \\
      --secret <random-32-byte-hex>

The API key comes from LS dashboard → Settings → API. Use the same
secret value when you `wrangler secret put LS_WEBHOOK_SECRET` on the
billing Worker so signature verification matches.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from typing import Any

import httpx

LS_BASE = os.environ.get("LEMONSQUEEZY_API_BASE", "https://api.lemonsqueezy.com/v1")
TIMEOUT = 30


def _client() -> httpx.Client:
    key = os.environ.get("LEMONSQUEEZY_API_KEY", "").strip()
    if not key:
        print("LEMONSQUEEZY_API_KEY env var is not set.", file=sys.stderr)
        print("Get one from: LS dashboard → Settings → API → New API Key", file=sys.stderr)
        sys.exit(2)
    return httpx.Client(
        base_url=LS_BASE,
        headers={
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
            "Authorization": f"Bearer {key}",
        },
        timeout=TIMEOUT,
    )


# ── Inventory: list every product + variant we'd potentially gate against ──

def _fetch_all(client: httpx.Client, path: str, params: dict | None = None) -> list[dict]:
    """Paginate through an LS list endpoint and collect every record.

    LS uses JSON:API pagination — `links.next` is the URL of the next
    page when more records exist. Default page size is 10; we ask for
    100 to keep round-trips low for SassyMCP-sized catalogs.
    """
    out: list[dict] = []
    qp = dict(params or {})
    qp.setdefault("page[size]", 100)
    url = path
    while url:
        resp = client.get(url, params=qp if url == path else None)
        resp.raise_for_status()
        body = resp.json()
        out.extend(body.get("data", []))
        url = (body.get("links") or {}).get("next")
        qp = None  # next link already has the params baked in
    return out


def cmd_list(_args) -> int:
    with _client() as c:
        stores = _fetch_all(c, "/stores")
        if not stores:
            print("No stores found on this account.", file=sys.stderr)
            return 1

        print("=== Stores ===")
        for s in stores:
            a = s.get("attributes", {})
            print(f"  store_id={s['id']:>10}  name={a.get('name')!r:30}  domain={a.get('domain')}")

        products = _fetch_all(c, "/products")
        print(f"\n=== Products ({len(products)}) ===")
        for p in products:
            a = p.get("attributes", {})
            print(f"  product_id={p['id']:>10}  name={a.get('name')!r:40}  store_id={a.get('store_id')}  status={a.get('status')}")

        variants = _fetch_all(c, "/variants")
        print(f"\n=== Variants ({len(variants)}) ===")
        # Index products by id so we can label variants with their product name
        prod_by_id = {p["id"]: p.get("attributes", {}).get("name", "?") for p in products}
        for v in variants:
            a = v.get("attributes", {})
            pid = str(a.get("product_id"))
            price = a.get("price") or 0
            interval = a.get("interval") or "one-time"
            print(
                f"  variant_id={v['id']:>10}  name={a.get('name')!r:30}  "
                f"product={prod_by_id.get(pid, '?')!r}  "
                f"price=${price/100:.2f}  interval={interval}  "
                f"status={a.get('status')}"
            )
    return 0


# ── Variant → entitlement map generator ───────────────────────────────────

# Heuristic name patterns. Adjust if your product naming diverges from
# the defaults SassyMCP's CHANGELOG advertises. Each match is checked
# against the lowercased "product_name | variant_name" string.
ENTITLEMENT_HEURISTICS = [
    # (substring patterns that must all match, entitlement)
    (("forensics",), {"tier": "free", "addons": ["forensics"]}),  # standalone forensics
    (("pro", "forensics"), {"tier": "pro", "addons": ["forensics"]}),  # bundle
    (("pro",), {"tier": "pro", "addons": []}),
    (("free",), {"tier": "free", "addons": []}),
]


def _classify(label: str) -> dict | None:
    """Map a product/variant label to a {tier, addons} entitlement.

    Most specific wins: bundle ("pro" + "forensics") is checked before
    plain "pro", so a "SassyMCP Pro + Forensics" variant gets the right
    bundle entitlement instead of just `tier=pro`.
    """
    lc = label.lower()
    best: tuple[int, dict] | None = None
    for patterns, entitlement in ENTITLEMENT_HEURISTICS:
        if all(p in lc for p in patterns):
            score = len(patterns)
            if best is None or score > best[0]:
                best = (score, entitlement)
    return best[1] if best else None


def cmd_variants(_args) -> int:
    """Print the DEFAULT_VARIANT_MAP block, ready to paste into
    sassymcp/_lemonsqueezy.py.

    For each variant whose name we can classify, we emit the mapping.
    Unclassifiable variants are written as commented-out lines so the
    operator can fix the heuristic or hand-edit the entry rather than
    silently dropping the variant.
    """
    with _client() as c:
        products = _fetch_all(c, "/products")
        prod_by_id = {p["id"]: p.get("attributes", {}).get("name", "?") for p in products}
        variants = _fetch_all(c, "/variants")

    print("DEFAULT_VARIANT_MAP: dict[str, dict[str, Any]] = {")
    unmapped: list[str] = []
    for v in variants:
        a = v.get("attributes", {})
        pid = str(a.get("product_id"))
        pname = prod_by_id.get(pid, "?")
        vname = a.get("name") or ""
        label = f"{pname} | {vname}"
        ent = _classify(label)
        comment = f'"{label}" — ${(a.get("price") or 0)/100:.2f}/{a.get("interval") or "one-time"}'
        if ent:
            print(f'    # {comment}')
            print(f'    "{v["id"]}": {{"tier": {ent["tier"]!r}, "addons": {ent["addons"]!r}}},')
        else:
            unmapped.append(f"{v['id']}: {label}")
            print(f'    # UNMAPPED: {comment}')
            print(f'    # "{v["id"]}": {{"tier": "free", "addons": []}},  # FIXME')
    print("}")

    if unmapped:
        print("\n# Could not auto-classify these variants:", file=sys.stderr)
        for u in unmapped:
            print(f"#   {u}", file=sys.stderr)
        print("# Add the corresponding tier/addons by hand before pasting "
              "into sassymcp/_lemonsqueezy.py.", file=sys.stderr)
    return 0


# ── Webhook subscription creation ─────────────────────────────────────────

DEFAULT_EVENTS = [
    # One-time purchase model — no subscription_* events needed.
    "order_created",       # license issued, ensure ACTIVE
    "order_refunded",      # revoke
    "license_key_created", # ACTIVE on creation
    "license_key_updated", # follow inner status: active/inactive/expired/disabled
]


def cmd_webhook(args) -> int:
    """Create (or update) the LS → billing Worker webhook subscription.

    Idempotent: if a webhook already exists for the same URL on the
    same store, we update it in place rather than creating a duplicate.
    LS returns the signing secret on create — print it once so the
    operator can pipe it to `wrangler secret put LS_WEBHOOK_SECRET`.
    """
    secret = args.secret or secrets.token_hex(32)
    if not args.secret:
        print(f"# (no --secret given; generated a fresh one)")

    with _client() as c:
        stores = _fetch_all(c, "/stores")
        if not stores:
            print("No stores on this account; create one in the LS dashboard first.",
                  file=sys.stderr)
            return 1
        # Use the first store unless --store-id is provided
        store_id = args.store_id or stores[0]["id"]
        store_name = next(
            (s.get("attributes", {}).get("name") for s in stores if s["id"] == store_id),
            "?",
        )
        print(f"# Using store: {store_name} (id={store_id})", file=sys.stderr)

        # Check for an existing webhook to the same URL — update if so
        existing = _fetch_all(
            c, "/webhooks",
            params={"filter[store_id]": store_id},
        )
        match = next(
            (w for w in existing if w.get("attributes", {}).get("url") == args.url),
            None,
        )

        payload = {
            "data": {
                "type": "webhooks",
                "attributes": {
                    "url": args.url,
                    "events": args.events.split(",") if args.events else DEFAULT_EVENTS,
                    "secret": secret,
                    "test_mode": args.test_mode,
                },
                "relationships": {
                    "store": {"data": {"type": "stores", "id": store_id}},
                },
            }
        }

        if match:
            print(f"# Webhook to {args.url} exists (id={match['id']}); updating in place.",
                  file=sys.stderr)
            payload["data"]["id"] = match["id"]
            resp = c.patch(f"/webhooks/{match['id']}", json=payload)
        else:
            print(f"# Creating new webhook subscription -> {args.url}", file=sys.stderr)
            resp = c.post("/webhooks", json=payload)

        if resp.status_code >= 400:
            print(f"LS API error {resp.status_code}: {resp.text}", file=sys.stderr)
            return 1

        body = resp.json()
        attrs = body.get("data", {}).get("attributes", {})
        print(json.dumps({
            "webhook_id": body.get("data", {}).get("id"),
            "url": attrs.get("url"),
            "events": attrs.get("events"),
            "test_mode": attrs.get("test_mode"),
            "secret": secret,
            "wrangler_command": (
                f"echo {secret} | wrangler secret put LS_WEBHOOK_SECRET "
                "--cwd sassymcp-billing"
            ),
        }, indent=2))
    return 0


# ── CLI plumbing ─────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="lemonsqueezy_setup",
        description="Post-dashboard LS setup for SassyMCP — variant capture + webhook wiring.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Inventory stores, products, variants").set_defaults(func=cmd_list)
    sub.add_parser("variants", help="Emit DEFAULT_VARIANT_MAP for _lemonsqueezy.py").set_defaults(func=cmd_variants)

    wh = sub.add_parser("webhook", help="Create/update the LS webhook subscription")
    wh.add_argument("--url", required=True, help="Public URL of the billing Worker webhook endpoint")
    wh.add_argument("--secret", default="", help="Signing secret (random 64-hex generated if omitted)")
    wh.add_argument("--events", default="", help=f"Comma-separated event names. Default: {','.join(DEFAULT_EVENTS)}")
    wh.add_argument("--store-id", default="", help="LS store id; defaults to the first one on the account")
    wh.add_argument("--test-mode", action="store_true", help="Subscribe to LS test-mode events instead of live")
    wh.set_defaults(func=cmd_webhook)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
