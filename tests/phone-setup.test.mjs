import assert from "node:assert/strict";
import test from "node:test";
import {
  isIOSSafari,
  phoneSetupUrl,
  dismissWelcome,
  welcomeDismissed,
} from "../web/src/phone-setup.js";

test("phone QR uses only the authenticated HTTPS origin and carries no session", () => {
  assert.equal(
    phoneSetupUrl(
      "https://vela.home:7700/app/private?token=secret#secret",
      true,
    ),
    "https://vela.home:7700/setup",
  );
  assert.equal(
    phoneSetupUrl("https://192.168.1.50:7700", true),
    "https://192.168.1.50:7700/setup",
  );
  for (const origin of [
    "http://vela.home:7700",
    "https://localhost:7700",
    "https://127.0.0.1",
    "https://127.8.9.1",
    "https://[::1]",
    "https://[::]",
    "https://0.0.0.0",
    "https://test.localhost",
    "https://user:password@vela.home",
    "invalid",
  ]) {
    assert.equal(phoneSetupUrl(origin, true), null, origin);
  }
  assert.equal(phoneSetupUrl("https://vela.home", false), null);
});

test("Safari instructions distinguish alternate browsers and embedded webviews", () => {
  const safari =
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1";
  assert.equal(isIOSSafari(safari), true);
  assert.equal(
    isIOSSafari(
      safari.replace(
        "iPhone; CPU iPhone OS 18_0",
        "Macintosh; Intel Mac OS X 10_15",
      ),
    ),
    true,
  );
  for (const browser of [
    "CriOS/129.0",
    "FxiOS/129.0",
    "EdgiOS/129.0",
    "OPiOS/1",
    "GSA/1",
    "Instagram",
    "FBAN/FBIOS",
    "DuckDuckGo/7",
  ]) {
    assert.equal(isIOSSafari(`${safari} ${browser}`), false, browser);
  }
  assert.equal(isIOSSafari(safari.replace("Version/18.0 ", "")), false);
});

test("managed Wi-Fi QR permits only a private HTTP bootstrap address", () => {
  assert.equal(
    phoneSetupUrl("http://192.168.1.20:7701/setup", true, {
      allowLocalHttp: true,
    }),
    "http://192.168.1.20:7701/setup",
  );
  for (const url of [
    "http://example.com",
    "http://127.0.0.1:7701",
    "http://8.8.8.8",
    "http://user:password@192.168.1.20",
  ]) {
    assert.equal(phoneSetupUrl(url, true, { allowLocalHttp: true }), null);
  }
});

test("blocked storage cannot prevent using or dismissing the welcome guide", () => {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    get() {
      throw new Error("Storage blocked");
    },
  });
  assert.equal(welcomeDismissed(), false);
  assert.doesNotThrow(dismissWelcome);
  assert.equal(welcomeDismissed(), true);
  delete globalThis.localStorage;
});
