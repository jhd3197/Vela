/**
 * The executors Vela is willing to run.
 *
 * Two sources, both reviewed by node id rather than by category:
 *
 *  - A short allow-list of Tramo built-ins that contain no dynamic code
 *    evaluation (`manual-trigger`, `template`, `log`, `json-stringify`,
 *    `delay`, `merge`, `approval-gate`).
 *  - Vela's own executors for branching and for effects, which are declarative
 *    and delegate every side effect back to the host.
 *
 * Everything else in Tramo's built-in pack — `js-transform`, `if`, `switch`,
 * `json-parse`, the loop and for-each family, the state variables, `call-flow`,
 * `http-request`, `mcp-tool-call` and the AI nodes — is deliberately absent.
 * Those paths compile configuration strings with `new Function`, or reach the
 * network directly. Leaving them out of the registry (rather than merely out of
 * the picker) is what makes them unreachable: the runner looks an executor up by
 * node type and fails the node when it is missing.
 */

import { BUILTIN_EXECUTORS, createExecutorRegistry, renderTemplate } from '@tramo/runtime';

/** Tramo built-ins that are safe to register verbatim. */
export const ALLOWED_BUILTIN_IDS = Object.freeze([
  'manual-trigger',
  'template',
  'log',
  'json-stringify',
  'delay',
  'merge',
  'approval-gate',
]);

/** Node ids Vela implements itself. */
export const VELA_EXECUTOR_IDS = Object.freeze([
  'vela-schedule-trigger',
  'vela-webhook-trigger',
  'vela-condition',
  'vela-notify',
  'vela-app-action',
]);

const MAX_OUTPUT_BYTES = 256 * 1024;
const MAX_TEMPLATE_BYTES = 32 * 1024;

function measure(value) {
  if (value === undefined) return 0;
  let encoded;
  try {
    encoded = JSON.stringify(value);
  } catch {
    throw new Error('node produced a value that cannot be serialized as JSON');
  }
  if (encoded === undefined) return 0;
  return Buffer.byteLength(encoded, 'utf8');
}

/** Reject oversized node output before it reaches the event stream or a checkpoint. */
function bounded(executor) {
  return {
    id: executor.id,
    execute: async (ctx) => {
      const result = await executor.execute(ctx);
      if (result === undefined) return result;
      const size = measure(result);
      if (size > MAX_OUTPUT_BYTES) {
        throw new Error(
          `node output of ${size} bytes exceeds the ${MAX_OUTPUT_BYTES} byte limit for one step`,
        );
      }
      return result;
    },
  };
}

/** Resolve `a.b.c` against the node input, `vars.` or `steps.` without evaluating code. */
export function resolvePath(expression, ctx) {
  const segments = String(expression ?? '')
    .split('.')
    .map((part) => part.trim())
    .filter(Boolean);
  if (segments.length === 0) return ctx.inputs.in;
  let cursor;
  let path;
  if (segments[0] === 'vars') {
    cursor = ctx.vars;
    path = segments.slice(1);
  } else if (segments[0] === 'steps') {
    cursor = ctx.steps;
    path = segments.slice(1);
  } else if (segments[0] === 'input') {
    cursor = ctx.inputs.in;
    path = segments.slice(1);
  } else {
    cursor = ctx.inputs.in;
    path = segments;
  }
  for (const segment of path) {
    if (cursor === null || typeof cursor !== 'object') return undefined;
    cursor = cursor[segment];
  }
  return cursor;
}

function renderBounded(template, ctx) {
  const text = String(template ?? '');
  if (Buffer.byteLength(text, 'utf8') > MAX_TEMPLATE_BYTES) {
    throw new Error('template exceeds the 32 KiB limit');
  }
  const rendered = renderTemplate(text, ctx.inputs.in, ctx.vars, ctx.steps);
  if (Buffer.byteLength(rendered, 'utf8') > MAX_TEMPLATE_BYTES) {
    throw new Error('rendered text exceeds the 32 KiB limit');
  }
  return rendered;
}

function coerce(raw, kind) {
  if (kind === 'number') {
    const value = Number(raw);
    if (Number.isNaN(value)) throw new Error(`"${raw}" is not a number`);
    return value;
  }
  if (kind === 'boolean') return raw === true || raw === 'true' || raw === 1 || raw === '1';
  if (kind === 'json') {
    try {
      return JSON.parse(String(raw));
    } catch {
      throw new Error('comparison value is not valid JSON');
    }
  }
  return raw === undefined || raw === null ? '' : String(raw);
}

function isEmpty(value) {
  if (value === undefined || value === null || value === '') return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === 'object') return Object.keys(value).length === 0;
  return false;
}

const COMPARISONS = {
  equals: (left, right) => JSON.stringify(left ?? null) === JSON.stringify(right ?? null),
  'not-equals': (left, right) => JSON.stringify(left ?? null) !== JSON.stringify(right ?? null),
  contains: (left, right) => String(left ?? '').includes(String(right ?? '')),
  'not-contains': (left, right) => !String(left ?? '').includes(String(right ?? '')),
  'starts-with': (left, right) => String(left ?? '').startsWith(String(right ?? '')),
  'ends-with': (left, right) => String(left ?? '').endsWith(String(right ?? '')),
  'greater-than': (left, right) => Number(left) > Number(right),
  'greater-or-equal': (left, right) => Number(left) >= Number(right),
  'less-than': (left, right) => Number(left) < Number(right),
  'less-or-equal': (left, right) => Number(left) <= Number(right),
  'is-empty': (left) => isEmpty(left),
  'is-not-empty': (left) => !isEmpty(left),
  'is-true': (left) => left === true || left === 'true',
  'is-false': (left) => left === false || left === 'false',
};

export const COMPARISON_OPERATORS = Object.freeze(Object.keys(COMPARISONS));

const UNARY = new Set(['is-empty', 'is-not-empty', 'is-true', 'is-false']);

/**
 * Declarative branch. Reads one path, compares it with a fixed operator and a
 * literal, and routes the untouched input to `true` or `false`. No expression
 * is compiled, so a workflow author cannot reach the host process through it.
 */
const condition = {
  id: 'vela-condition',
  execute: (ctx) => {
    const operator = String(ctx.config.operator ?? 'equals');
    const compare = COMPARISONS[operator];
    if (!compare) throw new Error(`unsupported comparison "${operator}"`);
    const left = resolvePath(ctx.config.subject, ctx);
    const right = UNARY.has(operator)
      ? undefined
      : coerce(renderBounded(ctx.config.value ?? '', ctx), String(ctx.config.valueType ?? 'string'));
    const matched = Boolean(compare(left, right));
    ctx.log.info(`condition ${operator} → ${matched ? 'true' : 'false'}`);
    return matched ? { true: ctx.inputs.in } : { false: ctx.inputs.in };
  },
};

/**
 * Trigger stubs. Vela dispatches schedules and webhooks itself and passes the
 * occurrence or request summary in as the run trigger; these executors only
 * forward it so the rest of the graph sees a normal value.
 */
const scheduleTrigger = {
  id: 'vela-schedule-trigger',
  execute: (ctx) => ({ out: ctx.inputs.in ?? { firedAt: Date.now() } }),
};

const webhookTrigger = {
  id: 'vela-webhook-trigger',
  execute: (ctx) => ({ out: ctx.inputs.in ?? { body: null, query: {}, receivedAt: Date.now() } }),
};

/** Ask the host to publish a notification through Vela's configured notifier. */
function notify(host, runId) {
  return {
    id: 'vela-notify',
    execute: async (ctx) => {
      const title = renderBounded(ctx.config.title ?? 'Vela automation', ctx).slice(0, 120);
      const message = renderBounded(ctx.config.message ?? '', ctx).slice(0, 4000);
      const priority = Math.min(5, Math.max(1, Number(ctx.config.priority ?? 3) || 3));
      ctx.log.info('requesting a notification from Vela');
      const receipt = await host.effect(
        {
          kind: 'notify',
          runId,
          nodeId: ctx.node.id,
          payload: { title, message, priority },
        },
        ctx.signal,
      );
      return { out: receipt };
    },
  };
}

/**
 * Ask the host to run one granted app action. The worker supplies rendered
 * input only; the host decides whether the automation may call it at all, and
 * derives the idempotency key so a retry cannot become a second write.
 */
function appAction(host, runId) {
  return {
    id: 'vela-app-action',
    execute: async (ctx) => {
      const [, app, action] = String(ctx.node.type).split(':');
      if (!app || !action) throw new Error('app action node is missing its app and action');
      // The inspector writes one flat `input.<field>` entry per form field.
      const input = {};
      for (const [key, raw] of Object.entries(ctx.config ?? {})) {
        if (!key.startsWith('input.')) continue;
        const field = key.slice('input.'.length);
        input[field] = typeof raw === 'string' ? renderBounded(raw, ctx) : raw;
      }
      ctx.log.info(`requesting ${app} · ${action}`);
      const receipt = await host.effect(
        {
          kind: 'app-action',
          runId,
          nodeId: ctx.node.id,
          payload: { app, action, input },
        },
        ctx.signal,
      );
      return { out: receipt };
    },
  };
}

/**
 * Build the registry for one run.
 *
 * `host.effect(request, signal)` is the only way out of the worker, and every
 * request names the run it belongs to so the host can authorize it against that
 * run's pinned revision.
 */
export function createVelaRegistry(host, runId) {
  const builtins = ALLOWED_BUILTIN_IDS.map((id) => {
    const executor = BUILTIN_EXECUTORS.find((candidate) => candidate.id === id);
    if (!executor) throw new Error(`Tramo no longer ships the "${id}" executor`);
    return executor;
  });
  const vela = [condition, scheduleTrigger, webhookTrigger, notify(host, runId),
                appAction(host, runId)];
  return createExecutorRegistry([...builtins, ...vela].map(bounded));
}

/** Node ids this worker can execute, for the host's startup compatibility check. */
export function supportedNodeIds() {
  return [...ALLOWED_BUILTIN_IDS, ...VELA_EXECUTOR_IDS];
}
