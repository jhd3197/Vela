import test from 'node:test';
import assert from 'node:assert/strict';
import { connectedAddress, connectedDocument } from '../web/src/connected-web.js';

test('connected views reject unsafe URLs and shared cookie hostnames', () => {
  for (const url of ['http://app.test', 'javascript:alert(1)', 'data:text/html,hi',
    'https://user:secret@app.test', 'https://vela.test:9999/', 'https://VELA.test/']) {
    assert.throws(() => connectedAddress(url, 'https://vela.test:7700'));
  }
  assert.equal(connectedAddress('https://app.test:8443/route?q=1#two', 'https://vela.test').origin, 'https://app.test:8443');
});

test('wrapper permits only its service origin and safely quotes untrusted text', () => {
  const doc = connectedDocument('https://app.test/path?q=1&next=%22', '\"><script>alert(1)</script>', 'https://vela.test');
  assert.match(doc, /frame-src https:\/\/app.test;/);
  assert.match(doc, /script-src &#39;none&#39;/);
  assert.match(doc, /sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-popups"/);
  assert.match(doc, /q=1&amp;next=%22/);
  assert.doesNotMatch(doc, /<script|allow-top-navigation|allow-popups-to-escape-sandbox|_vela\/sdk/);
});
