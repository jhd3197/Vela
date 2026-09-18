import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';
import { checkFoundations } from './assert-foundations.mjs';
import { TONES } from './fixtures/tones.js';

/**
 * The primitive layer, in every tone and both bases.
 *
 * What is checked is not that a mark looks right -- a screenshot does that --
 * but that the colour it ends up with is the one the token sheet resolves for
 * that tone. A primitive that hard-coded a violet would render perfectly in the
 * stock theme and stop following an imported one, and nothing but this
 * comparison would notice.
 */
async function checkPrimitives(page) {
  for (const base of ['light', 'dark']) {
    const gallery = page.locator(`[data-gallery="${base}"]`);
    await gallery.locator('.vela-card').first().waitFor();

    // The base really is applied to this subtree: the two galleries sit on one
    // page, so a scoping mistake would silently test the same base twice.
    const ground = await gallery.evaluate((node) =>
      getComputedStyle(node).getPropertyValue('--bg').trim(),
    );
    assert.ok(ground, `the ${base} gallery resolved no --bg`);

    for (const tone of TONES) {
      const card = gallery.locator(`[data-case="card-${tone}"]`);
      const measured = await card.evaluate((node) => {
        const read = (selector, property) => {
          const found = node.querySelector(selector);
          return found ? getComputedStyle(found).getPropertyValue(property) : null;
        };
        const icon = getComputedStyle(node.querySelector('.vela-card-icon'));
        return {
          declared: icon.getPropertyValue('--tone').trim(),
          soft: icon.getPropertyValue('--tone-soft').trim(),
          // Marks: a shape the eye finds by position as much as by colour.
          marks: {
            icon: icon.color,
            meterFill: read('.vela-meter-fill', 'background-color'),
            ringFill: read('.vela-ring-fill', 'stroke'),
            pillDot: read('.vela-pill-dot', 'background-color'),
            rowDot: read('.vela-row-dot', 'background-color'),
          },
          // Text: has to be read, so it takes the step that clears 4.5:1 on the
          // tint rather than the tone itself.
          inks: {
            tintedValue: read('.vela-kv-row:last-child .vela-kv-value', 'color'),
            pillLabel: read('.vela-pill', 'color'),
          },
        };
      });

      assert.ok(measured.declared, `${base}/${tone}: the tone resolved to nothing`);
      assert.ok(measured.soft, `${base}/${tone}: no soft tint`);

      // Every mark in the card is the one colour the tone names. Comparing the
      // marks to each other rather than to a value written here is what keeps
      // this true under a theme nobody has written yet.
      for (const [mark, value] of Object.entries(measured.marks)) {
        assert.ok(value, `${base}/${tone}: ${mark} drew nothing`);
        assert.equal(
          value,
          measured.marks.icon,
          `${base}/${tone}: ${mark} is ${value}, but the tone's mark is ${measured.marks.icon}`,
        );
      }

      // Text in a tone is the tone's legible step, and the two agree with each
      // other. For a status role the step and the tone are the same colour --
      // a green that can be read is already the green -- so this says they
      // agree, not that they differ.
      for (const [ink, value] of Object.entries(measured.inks)) {
        assert.ok(value, `${base}/${tone}: ${ink} drew nothing`);
        assert.equal(
          value,
          measured.inks.tintedValue,
          `${base}/${tone}: ${ink} is ${value}, but tinted text is ${measured.inks.tintedValue}`,
        );
      }
    }

    // Two tones must not resolve to the same colour, or the tone system is
    // decoration rather than meaning.
    const inks = await gallery.evaluate(
      (node, tones) =>
        tones.map(
          (tone) =>
            getComputedStyle(node.querySelector(`[data-case="card-${tone}"] .vela-card-icon`))
              .color,
        ),
      TONES,
    );
    assert.equal(new Set(inks).size, inks.length, `${base}: two tones resolved to one colour`);
  }
}

const web = fileURLToPath(new URL('..', import.meta.url));
const server = await createServer({
  configFile: false,
  root: web,
  plugins: [react()],
  css: { preprocessorOptions: { scss: { api: 'modern' } } },
  server: { host: '127.0.0.1', port: 0 },
});
let browser;
try {
  await server.listen();
  const { port } = server.httpServer.address();
  browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
  });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${port}/scripts/fixtures/shared-ui.html`);
  await page.waitForFunction(() => window.fixture?.reads.length === 1);

  await page.getByRole('button', { name: 'Ordinary button', exact: true }).click();
  assert.equal(await page.evaluate(() => fixture.submissions), 0);
  await page.getByRole('button', { name: 'Submit form', exact: true }).click();
  assert.equal(await page.evaluate(() => fixture.submissions), 1);
  const field = page.getByLabel('Server', { exact: true });
  assert.equal(await field.getAttribute('aria-invalid'), 'true');
  const help = await field.getAttribute('aria-describedby');
  assert.equal(help.split(' ').length, 3);
  for (const id of help.split(' '))
    assert.equal(await page.evaluate((id) => Boolean(document.getElementById(id)), id), true);
  assert.notEqual(
    await field.getAttribute('id'),
    await page.getByLabel('Topic', { exact: true }).getAttribute('id'),
  );

  // The first response arrives after switching IDs. It must never replace the second.
  await page.getByRole('button', { name: 'Switch resource', exact: true }).click();
  await page.waitForFunction(() => fixture.reads.length === 2);
  assert.equal(await page.evaluate(() => fixture.reads[0].signal.aborted), true);
  await page.evaluate(() => fixture.reads[1].resolve('second app'));
  await page.getByTestId('resource').filter({ hasText: 'second app' }).waitFor();
  await page.evaluate(() => fixture.reads[0].resolve('stale first app'));
  assert.equal(await page.getByTestId('resource').textContent(), 'second app');

  // Refresh joins an in-flight read, reports errors, then recovers on retry.
  await page.evaluate(() => {
    fixture.refresh();
    fixture.refresh();
  });
  await page.waitForFunction(() => fixture.reads.length === 3);
  await page.evaluate(() => fixture.reads[2].reject(new Error('offline')));
  await page.getByTestId('resource-error').filter({ hasText: 'offline' }).waitFor();
  assert.equal(await page.getByTestId('resource').textContent(), 'second app');
  await page.evaluate(() => {
    fixture.refresh();
  });
  await page.waitForFunction(() => fixture.reads.length === 4);
  await page.evaluate(() => fixture.reads[3].resolve('recovered'));
  await page.getByTestId('resource').filter({ hasText: 'recovered' }).waitFor();
  assert.equal(await page.getByTestId('resource-error').textContent(), '');
  await page.getByRole('button', { name: 'Toggle resource', exact: true }).click();
  assert.equal(await page.getByTestId('resource').textContent(), 'empty');
  await page.getByRole('button', { name: 'Toggle resource', exact: true }).click();
  await page.waitForFunction(() => fixture.reads.length === 5);
  await page.getByRole('status').filter({ hasText: 'Loading resource' }).waitFor();
  assert.equal(await page.getByTestId('resource').textContent(), 'empty');

  // Two submissions within one render still start only one operation.
  await page.evaluate(() => {
    fixture.submit();
    fixture.submit();
  });
  await page.waitForFunction(() => fixture.actions.length === 1);
  // The action starts before React has painted its pending state, so wait for
  // the render rather than racing it.
  const runAction = page.getByRole('button', { name: 'Run action', exact: true });
  await runAction.and(page.locator('[aria-busy="true"]')).waitFor();
  assert.equal(await runAction.isDisabled(), true);
  await page.evaluate(() => fixture.actions[0].reject(new Error('Action failed')));
  await page.getByRole('alert').filter({ hasText: 'Action failed' }).waitFor();
  await page.getByRole('button', { name: 'Run action', exact: true }).click();
  await page.waitForFunction(() => fixture.actions.length === 2);
  assert.equal(await page.getByRole('alert').filter({ hasText: 'Action failed' }).count(), 0);
  await page.evaluate(() => fixture.actions[1].resolve('saved'));
  await page.waitForFunction(() => fixture.completions === 1);

  await page.getByRole('button', { name: 'Run action', exact: true }).click();
  await page.waitForFunction(() => fixture.actions.length === 3);
  await page.getByRole('button', { name: 'Toggle action', exact: true }).click();
  await page.getByRole('button', { name: 'Toggle action', exact: true }).click();
  await page.evaluate(() => fixture.actions[2].resolve('old action'));
  assert.equal(await page.evaluate(() => fixture.completions), 1);
  assert.equal(
    await page.getByRole('button', { name: 'Run action', exact: true }).isEnabled(),
    true,
  );
  // useForm. An empty topic fails validation before anything is sent, and the
  // message appears under its own field.
  const topic = page.getByLabel('Hook topic', { exact: true });
  const hookForm = page.getByTestId('hook-form');
  await page.getByRole('button', { name: 'Save topic', exact: true }).click();
  await hookForm.getByRole('alert').filter({ hasText: 'Pick a topic.' }).waitFor();
  assert.equal(await topic.getAttribute('aria-invalid'), 'true');
  assert.equal(await page.evaluate(() => fixture.saves.length), 0);

  await topic.fill('alerts');
  assert.equal(await page.getByTestId('hook-form-state').textContent(), 'dirty idle');
  assert.equal(await topic.getAttribute('aria-invalid'), null, 'typing answers the complaint');

  // Two submits in one tick — the shape a double-clicked Save actually takes,
  // before React has committed the disabled button.
  await page.evaluate(() => {
    const form = document.querySelector('[data-testid="hook-form"]');
    form.requestSubmit();
    form.requestSubmit();
  });
  await page.waitForFunction(() => fixture.saves.length === 1);
  await page.waitForTimeout(50);
  assert.equal(await page.evaluate(() => fixture.saves.length), 1, 'a double submit sends once');
  assert.equal(await page.getByTestId('hook-form-state').textContent(), 'dirty saving');
  assert.equal(
    await page.getByRole('button', { name: 'Save topic', exact: true }).isDisabled(),
    true,
  );

  // A server complaint about one field lands on that field, not in a banner.
  await page.evaluate(() =>
    fixture.saves[0].reject(
      Object.assign(new Error('That did not save.'), {
        details: { fields: { topic: 'That topic is taken.' } },
      }),
    ),
  );
  await hookForm.getByRole('alert').filter({ hasText: 'That topic is taken.' }).waitFor();
  assert.equal(await topic.getAttribute('aria-invalid'), 'true');
  assert.equal(
    await page.getByTestId('hook-form-error').textContent(),
    'That did not save.',
    'the field keeps the detail; the form keeps what the request itself said',
  );
  assert.equal(await page.getByTestId('hook-form-state').textContent(), 'dirty idle');

  // A message with no field is about the form.
  await topic.fill('alerts-2');
  await page.getByRole('button', { name: 'Save topic', exact: true }).click();
  await page.waitForFunction(() => fixture.saves.length === 2);
  await page.evaluate(() => fixture.saves[1].reject(new Error('Cannot reach the Vela backend.')));
  await page
    .getByTestId('hook-form-error')
    .filter({ hasText: 'Cannot reach the Vela backend.' })
    .waitFor();
  assert.equal(await topic.getAttribute('aria-invalid'), null);

  // reset() makes the given values the new baseline.
  await page.getByRole('button', { name: 'Reset topic', exact: true }).click();
  assert.equal(await topic.inputValue(), 'saved');
  assert.equal(await page.getByTestId('hook-form-state').textContent(), 'clean idle');

  // useConfirm: one dialog for the whole app, Escape is no, and focus goes
  // back to whatever asked.
  const ask = page.getByRole('button', { name: 'Ask to remove', exact: true });
  const confirmDialog = page.getByRole('dialog', { name: 'Remove the fixture?', exact: true });
  const focused = (locator) => locator.evaluate((element) => element === document.activeElement);
  for (const [button, expected] of [
    ['Cancel', 'no'],
    ['Remove it', 'yes'],
  ]) {
    await ask.click();
    await confirmDialog.waitFor();
    assert.equal(
      await focused(confirmDialog.getByRole('button', { name: 'Cancel', exact: true })),
      true,
      'Cancel takes the initial focus',
    );
    await confirmDialog.getByRole('button', { name: button, exact: true }).click();
    await confirmDialog.waitFor({ state: 'hidden' });
    await page.getByTestId('confirm-answer').filter({ hasText: expected }).waitFor();
    assert.equal(await focused(ask), true, 'focus returns to the trigger');
  }
  await ask.click();
  await confirmDialog.waitFor();
  await page.keyboard.press('Escape');
  await confirmDialog.waitFor({ state: 'hidden' });
  await page.getByTestId('confirm-answer').filter({ hasText: 'no' }).waitFor();
  assert.equal(await focused(ask), true);

  await checkFoundations(page);
  await checkPrimitives(page);
  assert.deepEqual(errors, []);
  console.log(
    'PASS: native form behavior, accessible fields, strict-mode cleanup, stale reads, retry, duplicate actions and unmount safety',
  );
  console.log(
    'PASS: one submit per double-click, server field errors on their field, one confirm dialog with focus returned',
  );
  console.log(
    `PASS: every primitive in ${TONES.length} tones and both bases takes its colour from the token sheet`,
  );
} finally {
  await browser?.close();
  await server.close();
}
