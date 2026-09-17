/**
 * What an agent desktop's browser is allowed to reach.
 *
 * Two things only: the narrow gateway Vela publishes for app assets and bridge
 * effects, and the external origins the owner explicitly approved for this
 * desktop. Everything else is refused — the rest of this computer, the rest of
 * the home network, the browser's own debugging surfaces, and the owner
 * dashboard the gateway deliberately does not include.
 *
 * The decision is a pure function of a URL string so it can be tested directly
 * and so the same rule can be applied to a navigation, a subresource fetch, a
 * WebSocket handshake, a redirect hop and a popup target. Route interception
 * alone is not the boundary; it is one of the places the boundary is applied.
 *
 * What a URL cannot tell us is where a hostname actually resolves. A public name
 * pointing at 192.168.x.x is a real bypass, so `checkServerAddress` re-checks the
 * address the response actually came from and the session tears the page down
 * when it disagrees.
 */

/** Schemes that can reach the network. Anything else is refused outright. */
const NETWORK_SCHEMES = new Set(['http:', 'https:', 'ws:', 'wss:']);

/**
 * Schemes with no network reach that are still useful inside a page: a blank
 * frame, a blob a page made itself, an inline data URL.
 */
const INERT_SCHEMES = new Set(['about:', 'blob:', 'data:']);

export class PolicyError extends Error {}

/**
 * Normalize an origin the owner approved into `scheme://host[:port]`.
 *
 * Throws rather than silently accepting a bad rule: an unparseable site rule in
 * a policy is a configuration mistake, and treating it as "matches nothing" or
 * "matches everything" are both worse than saying so.
 */
export function normalizeOrigin(value) {
  let url;
  try {
    url = new URL(String(value));
  } catch {
    throw new PolicyError(`${value} is not a valid origin`);
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new PolicyError(`${value} must be http or https`);
  }
  if (!url.hostname) throw new PolicyError(`${value} has no host`);
  return url.port ? `${url.protocol}//${url.hostname}:${url.port}` : `${url.protocol}//${url.hostname}`;
}

/** The scheme/host/port of a URL, with ws(s) folded onto http(s) so one site rule covers both. */
function webOrigin(url) {
  const scheme = url.protocol === 'ws:' ? 'http:' : url.protocol === 'wss:' ? 'https:' : url.protocol;
  return url.port ? `${scheme}//${url.hostname}:${url.port}` : `${scheme}//${url.hostname}`;
}

/** Strip the brackets Node keeps on an IPv6 hostname. */
function bareHost(hostname) {
  const lower = String(hostname).toLowerCase();
  return lower.startsWith('[') && lower.endsWith(']') ? lower.slice(1, -1) : lower;
}

function ipv4Private(octets) {
  const [a, b] = octets;
  if (a === 0 || a === 127) return true; // this host, loopback
  if (a === 10) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  if (a === 169 && b === 254) return true; // link-local, including cloud metadata
  if (a === 100 && b >= 64 && b <= 127) return true; // carrier-grade NAT
  if (a >= 224) return true; // multicast and reserved
  return false;
}

/**
 * Parse every IPv4 spelling a browser still accepts: dotted quad, but also the
 * bare decimal, hex and octal forms that turn `127.0.0.1` into `2130706433`,
 * `0x7f000001` or `017700000001`. Returns octets, or null if this is not an
 * IPv4 literal at all.
 */
function parseIPv4(host) {
  const parts = host.split('.');
  if (parts.length === 0 || parts.length > 4) return null;
  const values = [];
  for (const part of parts) {
    if (part === '') return null;
    let value;
    if (/^0[xX][0-9a-fA-F]+$/.test(part)) value = parseInt(part, 16);
    else if (/^0[0-7]+$/.test(part)) value = parseInt(part, 8);
    else if (/^[0-9]+$/.test(part)) value = parseInt(part, 10);
    else return null;
    if (!Number.isFinite(value) || value < 0) return null;
    values.push(value);
  }
  // A short form packs the remaining octets into the last number:
  // `1.2.3` is 1.2.0.3 and `2130706433` is the whole address.
  const last = values.pop();
  if (last >= 256 ** (4 - values.length)) return null;
  if (values.some((value) => value > 255)) return null;
  const octets = [...values];
  const width = 4 - values.length;
  for (let index = width - 1; index >= 0; index -= 1) {
    octets.push(Math.floor(last / 256 ** index) % 256);
  }
  return octets;
}

/**
 * Parse an IPv6 literal into sixteen bytes, or null if it is not one.
 *
 * Written out rather than pattern-matched on the text because the address a
 * browser hands back is already normalized: `::ffff:127.0.0.1` arrives as
 * `::ffff:7f00:1`, and a check that only looked for a dotted tail would wave it
 * through.
 */
function parseIPv6(host) {
  if (!host.includes(':')) return null;
  const zone = host.indexOf('%');
  const text = zone === -1 ? host : host.slice(0, zone);
  const halves = text.split('::');
  if (halves.length > 2) return null;

  const expand = (part) => {
    if (part === '') return [];
    const groups = part.split(':');
    const bytes = [];
    for (let index = 0; index < groups.length; index += 1) {
      const group = groups[index];
      if (group.includes('.')) {
        if (index !== groups.length - 1) return null;
        const quad = parseIPv4(group);
        if (!quad || !group.includes('.')) return null;
        bytes.push(...quad);
        continue;
      }
      if (!/^[0-9a-f]{1,4}$/.test(group)) return null;
      const value = parseInt(group, 16);
      bytes.push((value >> 8) & 0xff, value & 0xff);
    }
    return bytes;
  };

  const head = expand(halves[0]);
  const tail = halves.length === 2 ? expand(halves[1]) : [];
  if (head === null || tail === null) return null;
  if (halves.length === 1) return head.length === 16 ? head : null;
  const gap = 16 - head.length - tail.length;
  if (gap < 0) return null;
  return [...head, ...new Array(gap).fill(0), ...tail];
}

/** Whether a hostname names this computer or a network only this computer can see. */
export function isPrivateHost(hostname) {
  const host = bareHost(hostname);
  if (!host) return true;
  if (host === 'localhost' || host.endsWith('.localhost')) return true;
  if (host.endsWith('.local') || host.endsWith('.internal') || host.endsWith('.home.arpa')) return true;

  const octets = parseIPv4(host);
  if (octets) return ipv4Private(octets);

  const bytes = parseIPv6(host);
  if (bytes) {
    const mapped = bytes.slice(0, 10).every((byte) => byte === 0) && bytes[10] === 0xff && bytes[11] === 0xff;
    if (mapped) return ipv4Private(bytes.slice(12)); // ::ffff:0:0/96
    if (bytes.every((byte) => byte === 0)) return true; // ::
    if (bytes.slice(0, 15).every((byte) => byte === 0) && bytes[15] === 1) return true; // ::1
    if (bytes.slice(0, 12).every((byte) => byte === 0)) return ipv4Private(bytes.slice(12)); // ::a.b.c.d
    if ((bytes[0] & 0xfe) === 0xfc) return true; // fc00::/7 unique-local
    if (bytes[0] === 0xfe && (bytes[1] & 0xc0) === 0x80) return true; // fe80::/10 link-local
    if (bytes[0] === 0xff) return true; // multicast
    return false;
  }
  return false;
}

/**
 * Build a checkable policy from the owner's desktop configuration.
 *
 * `gateway` is the one loopback origin this desktop may talk to, and only under
 * the path prefixes Vela publishes for it. `sites` are the approved external
 * origins; `includeSubdomains` is per rule and off by default, because
 * "example.com" is a decision about example.com.
 */
export function createPolicy({
  gatewayOrigin,
  gatewayPathPrefixes = ['/'],
  sites = [],
  allowPrivateSites = false,
} = {}) {
  const gateway = gatewayOrigin ? normalizeOrigin(gatewayOrigin) : null;
  const prefixes = gatewayPathPrefixes.map((prefix) => {
    const text = String(prefix);
    return text.startsWith('/') ? text : `/${text}`;
  });
  const approved = sites.map((site) => {
    const rule = typeof site === 'string' ? { origin: site } : site;
    const origin = normalizeOrigin(rule.origin);
    return {
      origin,
      host: new URL(origin).hostname.toLowerCase(),
      includeSubdomains: Boolean(rule.includeSubdomains),
      // What the agent may cause here, as opposed to what it may read. The
      // worker does not decide this; it carries it so a request can be
      // classified without a round trip for the ones that need none.
      effects: rule.effects === 'ask' ? 'ask' : 'read',
    };
  });
  return {
    gateway,
    gatewayPathPrefixes: prefixes,
    sites: approved,
    // A test seam, and deliberately a narrow one: it lets an approved origin be
    // on this machine, and does nothing else. Everything unapproved, including
    // the rest of loopback and the whole local network, is refused exactly as
    // before. Vela sets it only when its own environment says a fixture site is
    // running; a normal server never does.
    allowPrivateSites: Boolean(allowPrivateSites),
  };
}

/**
 * The approved-site rule a URL matches, or null.
 *
 * Separate from `decide` because two questions are asked of the same rule: may
 * this be requested at all, and what may be caused here. One lookup, so the two
 * answers cannot come from different rows.
 */
export function siteRuleFor(rawUrl, policy) {
  let url;
  try {
    url = new URL(String(rawUrl));
  } catch {
    return null;
  }
  const origin = webOrigin(url);
  for (const site of policy.sites || []) {
    if (origin === site.origin) return site;
    if (site.includeSubdomains) {
      const host = url.hostname.toLowerCase();
      const sameScheme = origin.startsWith(`${new URL(site.origin).protocol}//`);
      if (sameScheme && host.endsWith(`.${site.host}`)) return site;
    }
  }
  return null;
}

/**
 * Decide whether one URL may be requested under a policy.
 *
 * Returns `{ allowed, reason, target }`. `reason` is a stable short code so the
 * host can record which layer refused what without parsing prose.
 */
export function decide(rawUrl, policy) {
  let url;
  try {
    url = new URL(String(rawUrl));
  } catch {
    return { allowed: false, reason: 'unparseable_url', target: null };
  }

  if (INERT_SCHEMES.has(url.protocol)) {
    return { allowed: true, reason: 'inert_scheme', target: url.protocol };
  }
  if (!NETWORK_SCHEMES.has(url.protocol)) {
    // file:, chrome:, devtools:, view-source:, filesystem:, chrome-extension: …
    return { allowed: false, reason: 'scheme_denied', target: url.protocol };
  }

  const origin = webOrigin(url);

  if (policy.gateway && origin === policy.gateway) {
    const path = url.pathname || '/';
    const allowed = policy.gatewayPathPrefixes.some((prefix) => path.startsWith(prefix));
    return {
      allowed,
      reason: allowed ? 'gateway' : 'gateway_path_denied',
      target: origin,
    };
  }

  // The rule is looked up before the private-address refusal so that refusal
  // can say which of the two applies. An origin nobody approved is refused for
  // being unapproved whether or not it is also private.
  const site = siteRuleFor(rawUrl, policy);

  if (isPrivateHost(url.hostname) && !(site && policy.allowPrivateSites)) {
    // Everything else on this machine and this LAN, including the metadata
    // address, whatever spelling it arrives in.
    return { allowed: false, reason: 'private_network_denied', target: origin };
  }

  if (site) {
    const exact = origin === site.origin;
    return {
      allowed: true,
      reason: exact ? 'approved_site' : 'approved_subdomain',
      target: origin,
      site,
    };
  }

  return { allowed: false, reason: 'site_not_approved', target: origin };
}

/**
 * Re-check the address a response actually came from.
 *
 * A URL check cannot see DNS. An approved public hostname that resolves to a
 * private address is the classic way around an allowlist, so the session calls
 * this with `response.serverAddr()` and stops the view when it disagrees.
 */
export function checkServerAddress(rawUrl, ipAddress, policy) {
  if (!ipAddress) return { allowed: true, reason: 'no_address_reported' };
  let url;
  try {
    url = new URL(String(rawUrl));
  } catch {
    return { allowed: false, reason: 'unparseable_url' };
  }
  if (policy.gateway && webOrigin(url) === policy.gateway) {
    return { allowed: true, reason: 'gateway' };
  }
  if (isPrivateHost(ipAddress)) {
    if (policy.allowPrivateSites && siteRuleFor(rawUrl, policy)) {
      return { allowed: true, reason: 'approved_private_site' };
    }
    return { allowed: false, reason: 'private_address_resolved' };
  }
  return { allowed: true, reason: 'public_address' };
}
