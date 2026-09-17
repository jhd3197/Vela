/**
 * Describing a request an approved website is about to receive.
 *
 * The worker describes; Vela decides. That split is deliberate and is the whole
 * reason this file contains no policy: a worker that could decide what may be
 * sent would be a worker that could decide to send it, and the boundary would be
 * a comment rather than a mechanism.
 *
 * What travels to Vela is a shape, never contents. Method, URL, content type,
 * the *names* of the fields, the size, and a digest of the body. A password, a
 * card number and a verification code are all values, and none of them are in
 * that list — which matters, because what Vela receives here ends up in a prompt
 * somebody reads, and prompts get screenshotted.
 */

import { createHash } from 'node:crypto';

/** Methods that ask for something rather than change it. */
export const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

/** How much of a body is parsed for field names before it stops being read. */
export const MAX_PARSED_BODY = 256 * 1024;

/** Most field names described. A form with more is described by its first few. */
export const MAX_FIELDS = 12;

/**
 * Field names from a request body, without any of the values.
 *
 * Three shapes are understood and the rest are not described at all. "Not
 * described" is an answer Vela acts on — it is what makes an unclassifiable
 * request pause for a person instead of being summarized incorrectly.
 */
export function fieldNames(contentType, body) {
  const type = String(contentType || '').split(';')[0].trim().toLowerCase();
  if (!body || body.length === 0 || body.length > MAX_PARSED_BODY) return [];
  const text = body.toString('utf8');
  const names = [];
  try {
    if (type === 'application/x-www-form-urlencoded') {
      for (const key of new URLSearchParams(text).keys()) names.push(key);
    } else if (type === 'multipart/form-data') {
      // Names only, from the part headers. The parts themselves are never read:
      // one of them is usually a file the person chose.
      for (const match of text.matchAll(/name="([^"]{1,80})"/g)) names.push(match[1]);
    } else if (type === 'application/json') {
      const value = JSON.parse(text);
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        names.push(...Object.keys(value));
      }
    }
  } catch {
    return [];
  }
  const unique = [...new Set(names)];
  return unique.slice(0, MAX_FIELDS).map((name) => String(name).slice(0, 80));
}

/**
 * The description Vela is asked about.
 *
 * `requestId` is this worker's own handle on the paused request, so the answer
 * that comes back can be matched to the thing it is an answer about.
 */
export function describeRequest(request, { viewId = null } = {}) {
  const method = String(request.method() || 'GET').toUpperCase();
  const headers = request.headers();
  const contentType = headers['content-type'] || '';
  let body = null;
  try {
    body = request.postDataBuffer();
  } catch {
    body = null;
  }
  // A body containing a file is not readable from here at all: the browser holds
  // it as a stream and hands the worker nothing. That is reported as a fact
  // rather than as an empty body, because "no fields" and "fields Vela cannot
  // see" are different things and only one of them is safe to summarize.
  const hasBody = method !== 'GET' && method !== 'HEAD' && Boolean(headers['content-type']);
  const bodyAvailable = body !== null || !hasBody;
  const names = SAFE_METHODS.has(method) ? [] : fieldNames(contentType, body);
  return {
    method,
    url: request.url(),
    resourceType: request.resourceType(),
    navigation: Boolean(request.isNavigationRequest && request.isNavigationRequest()),
    contentType,
    fields: names,
    bodyAvailable,
    bodyBytes: body ? body.length : 0,
    bodyDigest: body ? createHash('sha256').update(body).digest('hex') : '',
    viewId,
  };
}

/** Whether this request is one Vela has to be asked about before it goes out. */
export function needsDecision(request) {
  return !SAFE_METHODS.has(String(request.method() || 'GET').toUpperCase());
}
