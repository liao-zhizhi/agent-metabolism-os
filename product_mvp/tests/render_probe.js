/* Execute the dashboard's D5 renderer and print what it would display.
 *
 * Why this exists: the honest test of "the page renders ``n_effective``" is to
 * run the renderer, not to search the source for a string. A grep passes on
 * dead code, on a branch nothing calls, and on a literal that the formatter
 * never emits -- which is the same class of fake coverage as the ``os.path
 * .isfile`` check this repository already had to delete.
 *
 * ``static/app.js`` is a plain browser script (no modules, no build step), so
 * it is loaded into a ``vm`` context with the handful of globals it touches at
 * load time stubbed out. Nothing is allowed to run at load: the boot code sits
 * behind ``DOMContentLoaded``, and ``setInterval`` is a no-op, so the probe
 * cannot poll a server or leave a timer alive.
 *
 *     node tests/render_probe.js '{"n_raw":6,"n_effective":1.0,"rho":1.0}'
 *     -> {"text":"1.0 / 6 (ρ=1.00)","tone":"calibrated",...}
 *
 * The payload is ``stats.independence`` exactly as ``GET /api/v1/stats`` sends
 * it, which is what keeps this probe from drifting away from the real field.
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP_JS = path.join(__dirname, "..", "static", "app.js");

function loadApp() {
  const noop = () => 0;
  const sandbox = {
    console,
    document: {
      // Nothing may run at load. ``boot`` waits for DOMContentLoaded, and this
      // stub simply never fires it.
      addEventListener: noop,
      getElementById: () => null,
      createElement: () => ({ style: {}, appendChild: noop, addEventListener: noop }),
    },
    sessionStorage: {
      getItem: () => null,
      setItem: noop,
      removeItem: noop,
    },
    navigator: {},
    window: {},
    crypto: { randomUUID: () => "probe-uuid" },
    setInterval: noop,
    clearInterval: noop,
    setTimeout: noop,
    clearTimeout: noop,
    fetch: () => Promise.reject(new Error("the render probe has no network")),
  };
  vm.createContext(sandbox);
  const source = fs.readFileSync(APP_JS, "utf8");
  vm.runInContext(source, sandbox, { filename: APP_JS });
  return sandbox;
}

function main() {
  const raw = process.argv[2];
  if (raw === undefined) {
    process.stderr.write("usage: node render_probe.js '<stats.independence json>'\n");
    process.exit(2);
  }
  const payload = JSON.parse(raw);
  const app = loadApp();
  if (typeof app.independenceText !== "function") {
    process.stderr.write(
      "app.js does not expose independenceText; the renderer is not a function\n"
    );
    process.exit(3);
  }
  const view = app.independenceText(payload);
  const tooltip = vm.runInContext("INDEPENDENCE_TOOLTIP", app);
  process.stdout.write(JSON.stringify({ ...view, tooltip }));
}

main();
