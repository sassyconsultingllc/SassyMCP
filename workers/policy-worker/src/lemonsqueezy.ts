// Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
// Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
// CodeMark: SCLLC1-SassyMCP-XZ1HO2KBXONT
//
// NOTE ON HEADER STYLE: the task asked for `#` comment headers to match repo
// convention, but `#` is not a valid comment in TypeScript (top-level `#`
// lines are a syntax error), so the identical three lines are kept here as
// `//` comments. package.json / tsconfig.json carry them as `_copyright`
// fields because JSON forbids comments entirely.

/**
 * LemonSqueezy license validation client.
 *
 * Mirrors the patterns in sassymcp/_lemonsqueezy.py:
 *   - LS license endpoints take x-www-form-urlencoded POSTs with
 *     `license_key` + `instance_id` and need NO API key (they are designed
 *     for client-side activation flows).
 *   - The authoritative status lives at `license_key.status`, one of
 *     "active" | "inactive" | "expired" | "disabled".
 *   - `meta.variant_id` identifies the purchased SKU; the worker maps it
 *     to a SassyMCP tier (see VARIANT_TIER in index.ts), defaulting to
 *     "free" for unmapped variants — same safe default as
 *     `variant_to_entitlement()` in the Python client.
 */

export const LS_BASE = "https://api.lemonsqueezy.com";
const LS_TIMEOUT_MS = 15_000;

export type LsLicenseStatus =
  | "active"
  | "inactive"
  | "expired"
  | "disabled"
  | "invalid" // LS answered valid=false with no recognizable status
  | "network_error" // fetch threw / timed out
  | "unknown"; // non-2xx, non-JSON, or otherwise unparseable

export interface LsValidation {
  status: LsLicenseStatus;
  /** Mirrors LS's top-level `valid` flag. */
  valid: boolean;
  /** Raw variant_id from `meta`, stringified. Null when LS didn't send one. */
  variantId: string | null;
  expiresAt: string | null;
  customerEmail: string | null;
  /** LS's own error message, or the transport error. Never the license key. */
  rawError: string | null;
}

function normalizeStatus(raw: unknown): LsLicenseStatus | null {
  if (
    raw === "active" ||
    raw === "inactive" ||
    raw === "expired" ||
    raw === "disabled"
  ) {
    return raw;
  }
  return null;
}

function empty(status: LsLicenseStatus, rawError: string | null): LsValidation {
  return {
    status,
    valid: false,
    variantId: null,
    expiresAt: null,
    customerEmail: null,
    rawError,
  };
}

/**
 * POST /v1/licenses/validate to LemonSqueezy.
 *
 * Never throws: transport failures are returned as
 * `{ status: "network_error", ... }` so the caller can fail closed with a
 * clear reason instead of crashing the worker.
 */
export async function validateLicense(
  licenseKey: string,
  instanceId: string,
): Promise<LsValidation> {
  const form = new URLSearchParams({
    license_key: licenseKey,
    instance_id: instanceId,
  });

  let resp: Response;
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), LS_TIMEOUT_MS);
    try {
      resp = await fetch(`${LS_BASE}/v1/licenses/validate`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: form.toString(),
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
    }
  } catch (e) {
    // Network failure — the caller treats this as "license service
    // unreachable" and fails closed (deny), mirroring the Python client's
    // refusal to brick on transport errors, but inverted: the worker is
    // the vendor trust anchor, so uncertainty denies rather than allows.
    return empty("network_error", e instanceof Error ? e.message : String(e));
  }

  let data: any = null;
  try {
    data = await resp.json();
  } catch {
    data = null;
  }

  if (!resp.ok || data === null || typeof data !== "object") {
    const msg =
      (data && typeof data.error === "string" && data.error) ||
      `LS returned HTTP ${resp.status} with an unparseable body`;
    return empty("unknown", msg);
  }

  const meta = data.meta ?? {};
  const lk = data.license_key ?? {};
  const shared = {
    variantId: meta.variant_id != null ? String(meta.variant_id) : null,
    expiresAt: typeof lk.expires_at === "string" ? lk.expires_at : null,
    customerEmail:
      typeof meta.customer_email === "string" ? meta.customer_email : null,
    rawError:
      typeof data.error === "string" && data.error ? data.error : null,
  };

  if (!data.valid) {
    return {
      ...shared,
      status: normalizeStatus(lk.status) ?? "invalid",
      valid: false,
    };
  }

  const status = normalizeStatus(lk.status);
  return {
    ...shared,
    status: status ?? "unknown",
    valid: true,
  };
}
