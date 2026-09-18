#!/usr/bin/env node
/**
 * Write the bundled themes from their base colours.
 *
 *   node web/scripts/build-themes.mjs
 *
 * Each theme here is six colours and a ground, not eighty hand-tuned values.
 * The surfaces, borders and inks are derived from the ground on the same
 * lightness stops the ramps use, so a theme is a decision about hue and depth
 * rather than a spreadsheet — and so a theme that reads well in one base cannot
 * quietly read badly in the other.
 *
 * Nothing is written until every theme passes the contrast gate in every base
 * it declares. A bundled theme that fails is a build failure, which is the
 * promise `docs/DESIGN.md` makes on the dashboard's behalf.
 *
 * Names follow the wallpapers' Venezuelan tradition without reusing a wallpaper
 * id: a theme is not a wallpaper, and one must never be mistaken for the other.
 */
import { mkdirSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { clampToGamut, contrast, flatten, toHex, toOklch, withAlpha } from '../src/design/color.js';
import { checkContrast, derive } from '../src/design/tokens.js';
import stock from '../src/design/theme.vela.json' with { type: 'json' };

const root = fileURLToPath(new URL('../..', import.meta.url));
export const THEME_DIR = path.join(root, 'vela/assets/themes');

/**
 * Where each surface, border and ink sits on the lightness scale, per base.
 *
 * Measured from the stock theme rather than invented: these are the lightnesses
 * its nine grounds, two borders and three inks actually have, rounded. A new
 * theme picking a different ground hue therefore lands on the same depths, and
 * looks like a Vela theme rather than like a different product.
 */
const DEPTHS = {
  light: {
    '--bg': 0.93,
    '--bg-rail': 0.918,
    '--bg-workspace': 0.971,
    '--bg-panel': 0.98,
    '--bg-header': 0.977,
    '--bg-card': 1,
    '--bg-field': 0.971,
    '--bg-inset': 0.93,
    '--bg-pop': 1,
    '--border': 0.869,
    '--border-strong': 0.779,
    '--text': 0.29,
    '--text-dim': 0.48,
    '--text-faint': 0.545,
    '--scrim': 0.195,
    '--glass': 0.992,
  },
  dark: {
    '--bg': 0.216,
    '--bg-rail': 0.175,
    '--bg-workspace': 0.242,
    '--bg-panel': 0.262,
    '--bg-header': 0.234,
    '--bg-card': 0.278,
    '--bg-field': 0.258,
    '--bg-inset': 0.258,
    '--bg-pop': 0.298,
    // A shade lighter than the stock theme's 0.335. Its border is a white wash
    // over navy and carries almost no chroma; a generated one keeps the
    // ground's, which costs it a little luminance against the card. The gate
    // asks for 1.3:1 so a card's edge can be found, and 0.335 came out at 1.22.
    '--border': 0.355,
    '--border-strong': 0.447,
    '--text': 0.942,
    '--text-dim': 0.799,
    '--text-faint': 0.709,
    '--scrim': 0.153,
    '--glass': 0.249,
  },
};

/**
 * The rail, the panel and the header are washes in the stock theme. The depths
 * above are what those washes actually come out at, measured over the ground,
 * so they are written here as the opaque colour rather than as an alpha: an
 * imported theme that is opaque everywhere is easier to reason about, and the
 * desk paints its own translucency over the wallpaper anyway.
 */
const WASHED = {};

/** How much of the ground's own chroma each depth keeps. */
const TINT = {
  '--text': 0.35,
  '--text-dim': 0.7,
  '--text-faint': 0.8,
  '--border': 0.8,
  '--border-strong': 0.8,
  '--scrim': 0.9,
  '--glass': 0.4,
};

const SYSTEM = {
  '--radius-sm': '9px',
  '--radius-md': '14px',
  '--radius-lg': '20px',
  '--font': stock.tokens.light['--font'],
  '--mono': stock.tokens.light['--mono'],
  '--on-wall': '#ffffff',
  '--on-accent': '#ffffff',
};

const SHADOWS = {
  light: {
    '--shadow-sm': '0 0 0 1px var(--border)',
    '--shadow-md': '0 0 0 1px var(--border), 0 6px 18px rgba(var(--scrim-rgb), 0.06)',
    '--shadow-lg': '0 0 0 1px var(--border), 0 16px 44px rgba(var(--scrim-rgb), 0.18)',
  },
  dark: {
    '--shadow-sm': '0 0 0 1px var(--border)',
    '--shadow-md': '0 0 0 1px var(--border)',
    '--shadow-lg': '0 0 0 1px var(--border-strong), 0 16px 44px rgba(0, 0, 0, 0.6)',
  },
};

const glow = (accent, cyan, base) =>
  base === 'light'
    ? `radial-gradient(720px 320px at 24% -10%, ${withAlpha(accent, 0.14)}, transparent 68%), ` +
      `radial-gradient(480px 280px at 96% 4%, ${withAlpha(cyan, 0.1)}, transparent 70%)`
    : `radial-gradient(760px 320px at 28% -12%, ${withAlpha(accent, 0.16)}, transparent 70%), ` +
      `radial-gradient(520px 260px at 96% 4%, ${withAlpha(cyan, 0.07)}, transparent 70%)`;

/** One base of one theme, from its ground and its six roles. */
function baseTokens(recipe, base) {
  const ground = toOklch(recipe.ground[base]);
  const depths = DEPTHS[base];
  const tokens = { ...SYSTEM, ...SHADOWS[base] };

  for (const [token, lightness] of Object.entries(depths)) {
    const chroma = ground.C * (TINT[token] ?? 1);
    // The same maths a ramp step is made with: hold the hue, put the colour at
    // this lightness, and lower the chroma until it fits in sRGB. That is what
    // keeps a surface and a ramp step of the same depth the same colour.
    const value = toHex(clampToGamut({ L: lightness, C: chroma, H: ground.H }));
    tokens[token] = WASHED[token] ? withAlpha(value, WASHED[token]) : value;
  }

  for (const [role, colour] of Object.entries(recipe.roles[base])) tokens[`--${role}`] = colour;
  tokens['--glow'] = glow(tokens['--accent'], tokens['--cyan'], base);
  return tokens;
}

/**
 * The bundled set: the stock theme, five more, and one built for contrast.
 *
 * Every one is generated from the same recipe, so none of them is hand-tuned
 * and none can be tuned into something the gate would not accept.
 */
export const RECIPES = [
  {
    slug: 'caribe',
    name: 'Caribe',
    description: 'Warm sand and sea. The green of shallow water for what is running.',
    suggests: 'medanos',
    ground: { light: '#f3ede2', dark: '#1a1c1e' },
    roles: {
      light: {
        neutral: '#9a958c',
        accent: '#0f7a86',
        cyan: '#0e7f6b',
        green: '#1c8f5a',
        amber: '#a8741a',
        red: '#c0432f',
      },
      dark: {
        neutral: '#9aa0a3',
        accent: '#3ecfd8',
        cyan: '#3fd4a8',
        green: '#46d58a',
        amber: '#e8b44a',
        red: '#e8705c',
      },
    },
  },
  {
    slug: 'tepuy',
    name: 'Tepuy',
    description: 'Wet stone and cloud forest, for a desk that should feel quiet.',
    suggests: 'canaima',
    ground: { light: '#eceef0', dark: '#161a1c' },
    roles: {
      light: {
        neutral: '#8f9599',
        accent: '#3f6d52',
        cyan: '#12707f',
        green: '#2a7d4f',
        amber: '#96701a',
        red: '#b34433',
      },
      dark: {
        neutral: '#98a3a8',
        accent: '#79c79a',
        cyan: '#4bc6d8',
        green: '#57cf8c',
        amber: '#dcae4e',
        red: '#e0705f',
      },
    },
  },
  {
    slug: 'cayena',
    name: 'Cayena',
    description: 'The hibiscus by the door. Warm, and louder than the stock look.',
    suggests: 'pueblo',
    ground: { light: '#f5ecec', dark: '#1d1618' },
    roles: {
      light: {
        neutral: '#9c9091',
        accent: '#b03b60',
        cyan: '#0d7686',
        green: '#25865a',
        amber: '#9e6f16',
        red: '#bf3f34',
      },
      dark: {
        neutral: '#a79899',
        accent: '#ff86a8',
        cyan: '#4fc9dc',
        green: '#4ed092',
        amber: '#e5ac44',
        red: '#f0766a',
      },
    },
  },
  {
    slug: 'llano',
    name: 'Llano',
    description: 'Dry grass at the end of the day, under a very large sky.',
    suggests: 'chiguire',
    ground: { light: '#f4f0e6', dark: '#1b1a16' },
    roles: {
      light: {
        neutral: '#99958a',
        accent: '#8a5a12',
        cyan: '#0c7382',
        green: '#2b8050',
        amber: '#96701a',
        red: '#b8412f',
      },
      dark: {
        neutral: '#a4a093',
        accent: '#e8b062',
        cyan: '#4ac6da',
        green: '#55cc88',
        amber: '#e3ae4c',
        red: '#e8705e',
      },
    },
  },
  {
    slug: 'sereno',
    name: 'Sereno',
    description: 'Night air off the mountain. Deep blue, and almost no colour.',
    suggests: 'avila',
    ground: { light: '#eceef4', dark: '#14171f' },
    roles: {
      light: {
        neutral: '#90949e',
        accent: '#3a5fb0',
        cyan: '#0f7286',
        green: '#268053',
        amber: '#8f6e1e',
        red: '#b34334',
      },
      dark: {
        neutral: '#99a0ad',
        accent: '#8fb2ff',
        cyan: '#4cc7dc',
        green: '#4fcd8a',
        amber: '#dfae50',
        red: '#e5735f',
      },
    },
  },
  {
    slug: 'contraste',
    name: 'Alto contraste',
    description: 'Built for reading. Every pair clears the gate with room to spare.',
    suggests: 'paramo',
    highContrast: true,
    ground: { light: '#f7f7f8', dark: '#0c0d10' },
    roles: {
      light: {
        neutral: '#6c7076',
        accent: '#4526c9',
        cyan: '#0a5f6e',
        green: '#12603c',
        amber: '#7a520c',
        red: '#9c1f14',
      },
      dark: {
        neutral: '#b7bcc4',
        accent: '#c4b4ff',
        cyan: '#79e6f8',
        green: '#7fe3ac',
        amber: '#f5c869',
        red: '#ff9c8c',
      },
    },
  },
];

/** A theme document from a recipe, with every base it declares. */
export function build(recipe) {
  const bases = Object.keys(recipe.ground);
  return {
    schema_version: 1,
    slug: recipe.slug,
    name: recipe.name,
    author: 'Vela',
    version: '1.0.0',
    description: recipe.description,
    bases,
    ...(recipe.suggests ? { suggests: { wallpaper: recipe.suggests } } : {}),
    tokens: Object.fromEntries(bases.map((base) => [base, baseTokens(recipe, base)])),
  };
}

/** Every bundled theme: the stock one as it is, and the recipes built. */
export function bundled() {
  return [stock, ...RECIPES.map(build)];
}

/** Each theme's failing contrast pairs, per base. Empty when the set is good. */
export function gate() {
  const failures = [];
  for (const theme of bundled()) {
    for (const base of theme.bases) {
      const derived = derive(theme.tokens[base], base);
      for (const failure of checkContrast(derived)) {
        failures.push({ slug: theme.slug, base, ...failure });
      }
      // A high-contrast theme promises more than the gate asks for, so it is
      // held to more: body text at 7:1 is the AAA bar, and a theme that says
      // "built for reading" and does not clear it is saying the wrong thing.
      const recipe = RECIPES.find((entry) => entry.slug === theme.slug);
      if (!recipe?.highContrast) continue;
      for (const surface of ['--bg-card', '--bg-workspace']) {
        const ratio = contrast(derived['--text'], flatten(derived[surface], derived['--bg']));
        if (ratio < 7) {
          failures.push({
            slug: theme.slug,
            base,
            ink: '--text',
            surface,
            min: 7,
            ratio: Math.round(ratio * 100) / 100,
          });
        }
      }
    }
  }
  return failures;
}

if (process.argv[1]?.endsWith('build-themes.mjs')) {
  const failures = gate();
  if (failures.length) {
    console.error('\nThese bundled themes do not clear the contrast gate:\n');
    for (const failure of failures) {
      console.error(
        `  ${failure.slug} (${failure.base}): ${failure.ink} on ${failure.surface} ` +
          `is ${failure.ratio}, needs ${failure.min}`,
      );
    }
    console.error('\nNothing was written.\n');
    process.exit(1);
  }
  mkdirSync(THEME_DIR, { recursive: true });
  for (const name of readdirSync(THEME_DIR)) {
    if (name.endsWith('.json')) rmSync(path.join(THEME_DIR, name));
  }
  for (const theme of bundled()) {
    writeFileSync(
      path.join(THEME_DIR, `${theme.slug}.json`),
      `${JSON.stringify(theme, null, 2)}\n`,
    );
  }
  console.log(`build-themes: wrote ${bundled().length} themes, all through the gate.`);
}
