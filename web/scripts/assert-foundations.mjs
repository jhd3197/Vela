import assert from 'node:assert/strict';

export async function checkFoundations(page) {
  await page.clock.install();
  await page.waitForFunction(() => window.foundationFixture.reads.length === 1);
  await page.evaluate(() => window.foundationFixture.reads[0].resolve({ apps_running: 2 }));
  await page.getByTestId('engine-home').filter({ hasText: '2' }).waitFor();
  assert.equal(await page.getByTestId('engine-shell').textContent(), '2');
  await page.getByRole('button', { name: 'Switch engine page', exact: true }).click();
  assert.equal(await page.getByTestId('engine-settings').textContent(), '2');
  assert.equal(await page.evaluate(() => window.foundationFixture.reads.length), 1);

  await page.clock.fastForward(10000);
  await page.waitForFunction(() => window.foundationFixture.reads.length === 2);
  await page.evaluate(() => window.foundationFixture.reads[1].reject(new Error('Engine offline')));
  await page.getByTestId('engine-error-settings').filter({ hasText: 'Engine offline' }).waitFor();
  assert.equal(await page.getByTestId('engine-error-shell').textContent(), 'Engine offline');
  assert.equal(await page.getByTestId('engine-shell').textContent(), '2');
  await page.getByRole('button', { name: 'Refresh shell', exact: true }).click();
  await page.waitForFunction(() => window.foundationFixture.reads.length === 3);
  assert.equal(
    await page.getByRole('button', { name: 'Refresh settings', exact: true }).isDisabled(),
    true,
  );
  await page.evaluate(() => window.foundationFixture.reads[2].resolve({ apps_running: 4 }));
  await page.getByTestId('engine-shell').filter({ hasText: '4' }).waitFor();
  assert.equal(await page.getByTestId('engine-settings').textContent(), '4');
  assert.equal(await page.getByTestId('engine-error-settings').textContent(), '');
  await page.getByRole('button', { name: 'Refresh settings', exact: true }).click();
  await page.waitForFunction(() => window.foundationFixture.reads.length === 4);
  await page.getByRole('button', { name: 'Toggle engine provider', exact: true }).click();
  assert.equal(await page.evaluate(() => window.foundationFixture.reads[3].signal.aborted), true);
  await page.evaluate(() => window.foundationFixture.reads[3].resolve({ apps_running: 99 }));
  await page.clock.fastForward(30000);
  assert.equal(await page.evaluate(() => window.foundationFixture.reads.length), 4);

  const drawer = page.getByRole('dialog', { name: 'Fixture drawer', exact: true });
  const nested = page.getByRole('dialog', { name: 'Nested dialog', exact: true });
  const opener = page.getByRole('button', { name: 'Open fixture drawer', exact: true });
  const overflow = await page.evaluate(() => document.documentElement.style.overflow);
  const focused = async (locator) =>
    locator.evaluate((element) => element === document.activeElement);
  for (const width of [1366, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await opener.click();
    await drawer.waitFor();
    assert.equal(
      await focused(drawer.getByRole('button', { name: 'Close fixture drawer', exact: true })),
      true,
    );
    await page.getByLabel('Background control').evaluate((element) => element.focus());
    assert.equal(
      await drawer.evaluate((element) => element.contains(document.activeElement)),
      true,
    );
    for (let i = 0; i < 7; i++) {
      await page.keyboard.press(i % 2 ? 'Shift+Tab' : 'Tab');
      assert.equal(
        await drawer.evaluate((element) => element.contains(document.activeElement)),
        true,
      );
    }

    await drawer.getByRole('button', { name: 'Open nested dialog', exact: true }).click();
    await nested.waitFor();
    assert.equal(
      await focused(nested.getByRole('button', { name: 'Cancel nested dialog', exact: true })),
      true,
    );
    await page.getByLabel('Dialog control').click();
    assert.equal(
      await drawer.isVisible(),
      true,
      'Clicks in a portal must not dismiss its parent drawer',
    );
    await nested.getByRole('button', { name: 'Toggle dialog pending', exact: true }).click();
    await page.keyboard.press('Escape');
    await page.mouse.click(5, 5);
    assert.equal(await nested.isVisible(), true, 'Pending dialog cannot dismiss');
    assert.equal(await drawer.isVisible(), true);
    await nested.getByRole('button', { name: 'Toggle dialog pending', exact: true }).click();
    await page.keyboard.press('Escape');
    await nested.waitFor({ state: 'hidden' });
    assert.equal(
      await focused(drawer.getByRole('button', { name: 'Open nested dialog', exact: true })),
      true,
    );
    assert.equal(await page.evaluate(() => document.documentElement.style.overflow), 'hidden');

    await drawer.getByRole('button', { name: 'Toggle drawer pending', exact: true }).click();
    assert.equal(await drawer.getAttribute('aria-busy'), 'true', 'Drawer enters pending state');
    await page.keyboard.press('Escape');
    assert.equal(await drawer.isVisible(), true, 'Pending drawer ignores Escape');
    await page.mouse.click(5, 5);
    assert.equal(await drawer.isVisible(), true, 'Pending drawer cannot dismiss');
    await drawer.getByRole('button', { name: 'Toggle drawer pending', exact: true }).click();
    // Dragging out from the panel is not a backdrop click.
    const box = await page.locator('.drawer').boundingBox();
    if (box.x > 5) {
      await page.mouse.move(box.x + 5, box.y + 5);
      await page.mouse.down();
      await page.mouse.move(5, 5);
      await page.mouse.up();
      assert.equal(await drawer.isVisible(), true);
      await page.mouse.click(5, 5);
    } else {
      // Phone drawers fill the viewport, so there is no exposed backdrop.
      await page.keyboard.press('Escape');
    }
    await drawer.waitFor({ state: 'hidden' });
    assert.equal(await focused(opener), true);
    assert.equal(await page.evaluate(() => document.documentElement.style.overflow), overflow);
  }
  console.log(
    'PASS: one engine request across consumers/routes, shared retries and cleanup; nested modal focus, keyboard, background isolation and pending guards at desktop/phone widths',
  );
}
