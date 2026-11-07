/* CEL console -- plain JS, no build step.
 *
 * Two pollers and one submit path:
 *   /api/v1/stats    every 2s   -> the metric cards
 *   /api/v1/ledger   every 2s   -> append-only terminal feed (only new seqs)
 *   /api/v1/verify   on click   -> a live verdict + the red alarm
 *
 * The feed is driven by the *ledger*, not by the click result: an entry that
 * only the clicker can see is not a monitor. What the operator sees on screen
 * is what the chain actually holds.
 */

const POLL_MS = 2000;
const MAX_LINES = 140;
const KEY_STORAGE = "cel_api_key";

const el = (id) => document.getElementById(id);

let lastSeq = 0;
/* Signature of which entries are refuted. A refutation does not add a seq to
 * the refuted entry itself, so the "only render seq > lastSeq" incremental path
 * would never repaint the line that turned red. Comparing a cheap signature is
 * how the feed notices that *history* changed, not just that it grew. */
let refutedSig = "";
let polling = false;
let booted = false;
let authEnabled = false;

/* Set when the server answered 403 to the stored key. Kept as state rather than
 * written straight into the DOM, because ``paintAuth`` runs on every 2s poll and
 * would otherwise wipe the rejection notice back to "saved" before the operator
 * could read it -- a stale "all good" over a key that does not work. */
let keyRejected = false;

/* The key lives in sessionStorage, never in the HTML: a page that ships the
 * credential is a page that hands it to anyone who can view source. sessionStorage
 * rather than localStorage, so closing the tab drops it instead of leaving a
 * write credential on a shared demo laptop. */
let apiKey = sessionStorage.getItem(KEY_STORAGE) || "";

/* ------------------------------------------------------------------ utils */

function shortHash(h) {
  if (!h) return "—";
  return h.slice(0, 10) + "…" + h.slice(-4);
}

function clockOf(ts) {
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function fmt(n, digits = 0) {
  if (n === null || n === undefined) return "—";
  return Number(n).toFixed(digits);
}

/* Entry fields are attacker-controlled: an agent chooses its own `intent`, and
 * an observer writes the `evidence` that gets rendered into a title attribute.
 * Interpolating either into HTML unescaped turns the audit log into an
 * injection point -- the one place where "it only displays strings" stops
 * being reassuring. */
function escapeAttr(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* Headers for a write. `X-API-Key` is omitted when no key is configured, which
 * is what makes the no-auth local demo work unchanged. */
function writeHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (apiKey) headers["X-API-Key"] = apiKey;
  return headers;
}

async function getJSON(url) {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

/* ------------------------------------------------------------- alarm state */

function flashAlarm() {
  document.body.classList.add("alarm");
  setTimeout(() => document.body.classList.remove("alarm"), 950);
}

let toastTimer = null;
function toast(message, kind = "fail") {
  const node = el("alarm-toast");
  node.textContent = message;
  node.className = "alarm-toast" + (kind === "pass" ? " is-pass" : kind === "warn" ? " is-warn" : "");
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 5200);
}

/* ----------------------------------------------------------------- metrics */

function paintStats(s) {
  el("m-intercepted").textContent = s.intercepted_false_completions ?? 0;

  const trust = Number(s.avg_trust_score ?? 100);
  el("m-trust").textContent = trust.toFixed(1);
  const bar = el("trust-bar");
  bar.style.width = Math.max(0, Math.min(100, trust)) + "%";
  bar.style.background =
    trust <= 50 ? "linear-gradient(90deg,#8c1d2b,#ff3b57)"
    : trust <= 80 ? "linear-gradient(90deg,#8a5c10,#ffb020)"
    : "linear-gradient(90deg,#17a85a,#2bff88)";

  el("m-inflamed").textContent = s.inflamed_agents ?? 0;
  el("m-inflamed-sub").textContent =
    `${s.locked_agents ?? 0} 个已锁定 / ${s.agents_total ?? 0} 个在册`;

  el("m-entries").textContent = s.total_entries ?? 0;
  const v = s.verdicts || {};
  const refuted = s.refuted_commitments ?? 0;
  // Refutations are appended to the verdict line rather than folded into the
  // PASS count. "PASS 5 · 其中 2 条已被推翻" is the auditable statement; showing
  // "PASS 3" would silently delete the two findings.
  el("m-verdicts").textContent =
    `PASS ${v.pass ?? 0} · FAIL ${v.fail ?? 0} · INVALID ${v.invalid ?? 0}` +
    (refuted ? ` · 已证伪 ${refuted}` : "");
  if (refuted) el("m-verdicts").classList.add("has-refuted");
  else el("m-verdicts").classList.remove("has-refuted");

  el("chain-head").textContent = shortHash(s.chain_head);
  el("uptime").textContent = "uptime " + Math.round(s.uptime_seconds ?? 0) + "s";
  el("f-version").textContent = "core " + (s.core_version || "—");

  const pill = el("health-pill");
  const state = String(s.health_state || "HEALTHY").toLowerCase();
  pill.className = "pill pill-" + state;
  el("health-text").textContent = (s.health_state || "HEALTHY");

  paintAuth(Boolean(s.auth_enabled));
  paintAgents(s.agents || []);
}

/* The server tells us which mode it is in; the page must never guess. An
 * unprotected instance looks identical to a protected one otherwise, and
 * "identical" is how someone demos an open port on a public URL. */
function paintAuth(enabled) {
  authEnabled = enabled;

  el("auth-banner").hidden = enabled;
  el("key-row").hidden = !enabled;

  const state = el("key-state");
  if (!enabled) {
    state.hidden = true;
    return;
  }

  state.hidden = false;
  if (!apiKey) {
    state.textContent = "未填写 key：模拟按钮会被 403 拒绝。粘贴服务器的 CEL_API_KEY 后点「保存」。";
    state.className = "key-state is-warn";
  } else if (keyRejected) {
    state.textContent = "这颗 key 被服务器拒绝（403）。核对后重新粘贴并保存。";
    state.className = "key-state is-warn";
  } else {
    state.textContent = "已保存 key（仅本次标签页有效，关闭即清除）。";
    state.className = "key-state is-ok";
  }
}

function paintAgents(agents) {
  const list = el("agents-list");
  if (!agents.length) {
    list.innerHTML = '<li class="agents-empty">尚无智能体提交过事务</li>';
    return;
  }

  list.innerHTML = "";
  for (const a of agents) {
    const li = document.createElement("li");
    li.className = "agent-row" + (a.inflamed ? " is-inflamed" : "");

    const badge = a.locked ? "locked" : String(a.penalty_state || "").toLowerCase();
    li.innerHTML = `
      <span class="agent-name" title="${a.agent_id}">${a.agent_id}</span>
      <span class="agent-trust">${a.trust_score === null ? "—" : fmt(a.trust_score, 1)}</span>
      <span class="badge ${badge}">${a.locked ? "LOCKED" : a.penalty_state}</span>
    `;

    if (a.locked) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "unlock-btn";
      btn.textContent = "解锁";
      btn.addEventListener("click", () => unlock(a.agent_id));
      li.appendChild(btn);
    }
    list.appendChild(li);
  }
}

/* -------------------------------------------------------------- live feed */

/* Evidence, not decoration. A PASS used to be indistinguishable from a PASS
 * that carried a digest, which was the console's own version of the bug the
 * ledger just fixed: the screen showed a verdict without showing what backed
 * it. Every line now says which of the three it is -- a recorded digest, an
 * honest negative with nothing to hash, or a dashboard simulation.
 *
 * ``shortHash`` is the existing 10+4 truncation used for the chain head, reused
 * so a digest looks the same everywhere on the page.
 */
function evidenceTag(entry) {
  const artefact = String(entry.artefact || "");
  const hash = String(entry.witness_hash || "");

  // A refutation outranks every other badge: the digest is still real, but the
  // thing it was supposed to point at turned out not to be. Saying "commit"
  // here would keep advertising a promise that has already been broken.
  if (entry.refuted) {
    const why = entry.refuted_reason === "hash_mismatch"
      ? "内容与承诺不符"
      : "产物不存在";
    return `<span class="ev-tag ev-refuted" title="🎯 已被事后核对推翻（${escapeAttr(why)}）：${escapeAttr(entry.refuted_evidence || "")}">✖ refuted</span>`;
  }

  // The refutation record itself. It is the finding, not a claim: showing a
  // digest badge here would read as "another commitment", when the line means
  // the opposite -- this is where a commitment was destroyed.
  if (entry.verdict === "REFUTED") {
    const why = entry.falsify_reason === "hash_mismatch" ? "hash_mismatch" : "file_not_found";
    return `<span class="ev-tag ev-refuted" title="推翻 #${escapeAttr(entry.refutes_seq)}：${escapeAttr(why)} — ${escapeAttr(entry.reason || "")}">🎯 refutes #${escapeAttr(entry.refutes_seq)}</span>`;
  }

  // A simulated preset must never look like physical evidence. It says so on
  // the line itself, so a screenshot cannot be cropped into a claim.
  if (artefact.startsWith("simulated://")) {
    return `<span class="ev-tag ev-sim" title="${escapeAttr(artefact)}">simulated://</span>`;
  }
  // The badge names what was recorded, not what was proven. An earlier version
  // rendered a bare chain glyph, which made an invented digest and an honest
  // one indistinguishable on screen -- the exact overclaim this whole exercise
  // is about. "commit" is the claim the server can actually stand behind.
  if (entry.verdict === "PASS" && entry.witness === true) {
    return hash
      ? `<span class="ev-tag ev-hash" title="commitment recorded: sha256:${escapeAttr(hash)} · trace ${escapeAttr(entry.trace_id || "—")} — the server cannot verify the file exists">⛓ commit ${shortHash(hash)}</span>`
      : `<span class="ev-tag ev-none">no digest</span>`;
  }
  if (entry.verdict === "PASS") {
    // Accurate bad news: there is no artefact, so no digest is expected.
    return `<span class="ev-tag ev-none" title="honest negative: nothing to hash">honest failure</span>`;
  }
  return "";
}

function renderEntry(entry, isNew) {
  const term = el("terminal");
  const line = document.createElement("div");
  const verdict = entry.verdict || "?";
  line.className = "term-line is-" + verdict.toLowerCase();
  if (entry.refuted) line.classList.add("is-refuted");

  const trust = entry.trust_after === null || entry.trust_after === undefined
    ? "—" : fmt(entry.trust_after, 1);

  const detail =
    verdict === "REFUTED" ? `<span class="term-tail term-tail-red">[${escapeAttr(entry.falsify_reason || "refuted")} · trust ${trust}]</span>`
    : verdict === "FAIL" ? `<span class="term-tail">[${entry.charged_as || "FAIL"} · trust ${trust}]</span>`
    : verdict === "REFUSED" ? `<span class="term-tail">[quarantined]</span>`
    : verdict === "UNLOCK" ? `<span class="term-tail">[manual release]</span>`
    : verdict === "INVALID" ? `<span class="term-tail">[no witness submitted]</span>`
    : entry.refuted ? `<span class="term-tail term-tail-red">[refuted → trust ${trust} at seal]</span>`
    : `<span class="term-tail">[trust ${trust}]</span>`;

  // The reconciliation affordance. Deliberately quiet and only present where it
  // can do something: a standing commitment with a digest, not yet refuted.
  // Putting it on FAIL or INVALID rows would imply those could be refuted,
  // and a refutation of an honest failure would be refuting the truth.
  const canRefute =
    verdict === "PASS" && entry.witness === true &&
    entry.witness_hash && !entry.refuted && entry.trace_id;

  const verifyBtn = canRefute
    ? `<button class="verify-btn" type="button" data-trace="${escapeAttr(entry.trace_id)}" data-agent="${escapeAttr(entry.agent_id)}" data-hash="${escapeAttr(entry.witness_hash)}" title="事后对账：拿到真实产物后回头核验这条承诺">对账</button>`
    : "";

  line.innerHTML = `
    <span class="seq">#${String(entry.seq).padStart(3, "0")}</span>
    <span class="t">${clockOf(entry.ts)}</span>
    <span class="v-tag v-${verdict.toLowerCase()}">${verdict}</span>
    ${evidenceTag(entry)}
    <span class="term-agent">${escapeAttr(entry.agent_id)}</span>
    <span class="term-intent">${escapeAttr(entry.intent)}</span>
    ${detail}
    ${verifyBtn}
    ${isNew ? '<span class="mark-new">NEW</span>' : ""}
  `;

  const btn = line.querySelector(".verify-btn");
  if (btn) btn.addEventListener("click", () => openFalsify(btn.dataset));

  term.appendChild(line);

  while (term.children.length > MAX_LINES) term.removeChild(term.firstChild);

  // Only pin to the bottom when the operator is already there: yanking the
  // scroll while someone is reading an older entry is how a log becomes useless.
  const nearBottom = term.scrollHeight - term.scrollTop - term.clientHeight < 120;
  if (nearBottom || isNew) term.scrollTop = term.scrollHeight;
}

function paintLedger(data) {
  const state = el("chain-state");
  state.textContent = data.chain_ok ? "chain ✓ sealed" : "chain ✗ TAMPERED";
  state.style.color = data.chain_ok ? "var(--green-dim)" : "var(--red)";

  // A refutation is a change to *existing* history, so it has to be detected
  // separately from "a new seq arrived". Without this the original line keeps
  // rendering as a green PASS forever, and the dashboard would be silently
  // disagreeing with the chain it is reading.
  //
  // The signature is always updated, including on the first paint: leaving it
  // stale means every later poll thinks history just changed and repaints the
  // whole feed, which would also steal the operator's scroll position.
  const sig = data.entries.filter((e) => e.refuted).map((e) => e.seq).join(",");
  if (sig !== refutedSig) {
    refutedSig = sig;
    if (booted) {
      booted = false;  // force a full repaint from the current entry set
      lastSeq = 0;
    }
  }

  const fresh = data.entries.filter((e) => e.seq > lastSeq);
  if (!fresh.length) return;

  if (!booted) {
    const term = el("terminal");
    term.innerHTML = "";
    fresh.forEach((e) => renderEntry(e, false));
    booted = true;
  } else {
    fresh.forEach((e) => renderEntry(e, true));
  }

  lastSeq = fresh[fresh.length - 1].seq;
}

/* --------------------------------------------------------- falsification */

/* The reconciliation loop, client side. Everything here is deliberately
 * awkward on purpose: this is the only control on the page that destroys a
 * record's standing, and it acts on a claim the server already accepted.
 * A one-click "refute" next to every line would turn the audit trail into a
 * moderation tool.
 */
let falsifyTarget = null;

function openFalsify(dataset) {
  falsifyTarget = {
    trace_id: dataset.trace || "",
    agent_id: dataset.agent || "",
    witness_hash: dataset.hash || "",
  };
  el("fs-agent").textContent = falsifyTarget.agent_id || "—";
  el("fs-trace").textContent = falsifyTarget.trace_id || "—";
  el("fs-hash").textContent = falsifyTarget.witness_hash || "—";
  el("fs-hash").title = falsifyTarget.witness_hash || "";
  el("fs-evidence").value = "";
  el("fs-observed").value = "";
  el("fs-error").hidden = true;
  el("fs-observed-wrap").hidden = true;
  document.querySelector('input[name="fs-reason"][value="file_not_found"]').checked = true;
  el("falsify-backdrop").hidden = false;
  el("fs-evidence").focus();
}

function closeFalsify() {
  el("falsify-backdrop").hidden = true;
  falsifyTarget = null;
}

async function submitFalsify() {
  if (!falsifyTarget) return;

  // Same pre-flight as verify: a 403 after the operator typed a paragraph of
  // evidence wastes the paragraph. Fail before the round trip.
  if (authEnabled && !apiKey) {
    toast("证伪是写操作：先填写 X-API-Key", "warn");
    el("api-key").focus();
    return;
  }

  const reason = document.querySelector('input[name="fs-reason"]:checked').value;
  const evidence = el("fs-evidence").value.trim();
  const observed = el("fs-observed").value.trim();

  // Mirrors the server's own refusals so the operator learns the rule before
  // the round trip. The server still enforces every one of these.
  const fail = (msg) => {
    const box = el("fs-error");
    box.textContent = msg;
    box.hidden = false;
  };
  if (!evidence) {
    fail("必须写下你查了什么、看到了什么。没有依据的证伪只是一句断言。");
    return;
  }
  if (reason === "hash_mismatch") {
    if (!observed) {
      fail("摘要不一致需要填写你实际算出的 SHA-256。");
      return;
    }
    if (observed === falsifyTarget.witness_hash) {
      fail("你填的摘要与承诺一致——那是确认，不是证伪。");
      return;
    }
  }

  const payload = { trace_id: falsifyTarget.trace_id, reason, evidence };
  if (reason === "hash_mismatch") payload.observed_hash = observed;

  const button = el("fs-submit");
  button.disabled = true;
  button.textContent = "提交中…";
  try {
    const res = await fetch("/api/v1/falsify", {
      method: "POST",
      headers: writeHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (res.status === 403) {
      authEnabled = true;
      keyRejected = true;
      paintAuth(authEnabled);
      fail("403：这颗 key 无权证伪，或服务器启用了别的凭据。");
      return;
    }
    if (!res.ok) {
      fail(String(data.detail || "证伪被拒绝（" + res.status + "）").split(".")[0]);
      return;
    }

    closeFalsify();
    flashAlarm();
    toast(
      `🎯 已证伪：${data.agent_id} · 信任分 ${fmt(data.trust_score, 1)} · ${data.message}`,
      "warn"
    );
    // Full repaint: the line that turned red is an *old* seq, so the
    // incremental path would leave it green until the next reload.
    refutedSig = "";
    booted = false;
    lastSeq = 0;
    await tick();
  } catch (err) {
    fail("无法连接网关：" + err.message);
  } finally {
    button.disabled = false;
    button.textContent = "提交证伪";
  }
}

/* ---------------------------------------------------------------- polling */

async function pollStats() {
  try {
    paintStats(await getJSON("/api/v1/stats"));
  } catch (err) {
    el("health-text").textContent = "OFFLINE";
    el("health-pill").className = "pill pill-critical";
  }
}

async function pollLedger() {
  try {
    paintLedger(await getJSON("/api/v1/ledger?limit=50"));
  } catch (err) {
    /* the stats poller already reports the outage */
  }
}

async function tick() {
  if (polling) return;
  polling = true;
  try {
    await Promise.all([pollStats(), pollLedger()]);
  } finally {
    polling = false;
  }
}

/* ----------------------------------------------------------------- submit */

/* The two presets that claim a landed effect need a digest and a trace id:
 * a success claim without one is refused with 422 by design (see
 * cel_engine.WitnessHashLimits). These are **simulations**, so the digest is a
 * constant over the literal string "cel-dashboard-simulation" and the artefact
 * is a `simulated://` pointer. That keeps the demo working without dressing a
 * button press up as a real grounding: the ledger entry itself says simulated.
 */
const SIM_HASH = "sha256:d19733cb344a38d43297e4071f12561ba0f76371b2fcd48e8c6493ec03646039";
const newTrace = () =>
  (crypto.randomUUID ? "sim-" + crypto.randomUUID()
                     : "sim-" + Date.now() + "-" + Math.random().toString(16).slice(2));

const SCENARIOS = {
  // The headline demo: the agent says it succeeded, the evidence says it did not.
  false_completion: {
    agent_id: "robot_arm_01",
    intent: "write to database",
    check: true,
    witness: false,
  },
  honest: {
    agent_id: "robot_arm_01",
    intent: "grasp the sample vial",
    check: false,
    witness: false,
  },
  verified: {
    agent_id: "robot_arm_01",
    intent: "seal the sample vial",
    check: true,
    witness: true,
    artefact: "simulated://vial/42",
    witness_hash: SIM_HASH,
    trace_id: newTrace(),
  },
  unwitnessed: {
    agent_id: "nav_stack_02",
    intent: "report corridor cleared",
    check: true,
    witness: null,
  },
  underclaim: {
    agent_id: "nav_stack_02",
    intent: "report battery swap",
    check: false,
    witness: true,
    artefact: "simulated://battery/02",
    witness_hash: SIM_HASH,
    trace_id: newTrace(),
  },
};

async function submit(payload, button) {
  // Fail before the round trip, so the operator gets a sentence instead of a 403.
  if (authEnabled && !apiKey) {
    toast("先填写 X-API-Key：这台服务器已启用鉴权，匿名写入会被 403 拒绝", "warn");
    el("api-key").focus();
    return;
  }

  if (button) button.disabled = true;
  try {
    const res = await fetch("/api/v1/verify", {
      method: "POST",
      headers: writeHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (res.status === 403) {
      // A 403 is direct evidence that this instance enforces a key -- stronger
      // than the stats poll, which may simply have failed to load. Trust it, and
      // show the field even if the panel thought auth was off.
      authEnabled = true;
      keyRejected = true;
      paintAuth(authEnabled);
      // First sentence only: the server's detail is written for an operator
      // reading a log, and a wall of it in a toast pushes the useful part away.
      toast("403 拒绝：" + String(data.detail || "X-API-Key 不正确").split(".")[0], "warn");
      return;
    }
    if (res.status === 423) {
      showVerdict(data);
      flashAlarm();
      toast(`⛔ 已拦截：${data.agent_id} 处于隔离状态，本次尝试未记账`);
      return;
    }
    if (!res.ok) {
      toast("请求被拒绝：" + (data.detail || res.status), "warn");
      return;
    }

    showVerdict(data);
    if (data.verdict === "FAIL") {
      flashAlarm();
      toast(`⛔ 拦截假完成：${data.agent_id} · ${data.message}`);
    } else if (data.verdict === "INVALID") {
      toast(`⚠ 无见证：${data.agent_id} · 条目无法判定，账本已锁`, "warn");
    } else {
      toast(`✓ ${data.verdict}：${data.agent_id} · trust ${fmt(data.trust_score, 1)}`, "pass");
    }

    // Pull the new entry from the chain immediately instead of waiting a beat.
    await tick();
  } catch (err) {
    toast("无法连接网关：" + err.message, "warn");
  } finally {
    if (button) button.disabled = false;
  }
}

function showVerdict(data) {
  const card = el("verdict-card");
  card.hidden = false;

  const v = String(data.verdict || "?").toLowerCase();
  card.className = "verdict-card is-" + v;

  const badge = el("v-badge");
  badge.textContent = data.verdict;
  badge.className = "v-badge v-" + v;

  el("v-agent").textContent = data.agent_id || "—";
  el("v-message").textContent = data.message || "—";
  el("v-trust").textContent = data.trust_score === null ? "—" : fmt(data.trust_score, 1);
  el("v-penalty").textContent = fmt(data.penalty ?? 0, 1);
  el("v-state").textContent = data.penalty_state || "—";
  el("v-seq").textContent = data.seq ?? "—";
  el("v-hash").textContent = data.chain_hash || "—";
  el("v-hash").title = data.chain_hash || "";
}

async function unlock(agentId) {
  if (authEnabled && !apiKey) {
    toast("解锁是写操作：先填写 X-API-Key", "warn");
    el("api-key").focus();
    return;
  }
  try {
    const res = await fetch(`/api/v1/agents/${encodeURIComponent(agentId)}/unlock`, {
      method: "POST",
      headers: writeHeaders(),
      body: JSON.stringify({ reason: "operator release from dashboard" }),
    });
    const data = await res.json();
    if (res.status === 403) {
      authEnabled = true;
      keyRejected = true;
      paintAuth(authEnabled);
      toast("403 拒绝：这颗 key 无权解锁", "warn");
      return;
    }
    if (!res.ok) {
      toast("解锁失败：" + (data.detail || res.status), "warn");
      return;
    }
    toast(`🔓 ${agentId} 已由人工解锁`, "pass");
    await tick();
  } catch (err) {
    toast("解锁请求失败：" + err.message, "warn");
  }
}

/* ------------------------------------------------------------- bootstrap */

document.addEventListener("DOMContentLoaded", () => {
  el("btn-simulate").addEventListener("click", (e) => {
    // Rotate intents so a demo clicked five times does not read as one entry
    // repeated five times.
    const intents = [
      "write to database",
      "delete stale rows",
      "deploy to production",
      "mark task complete",
      "commit migration",
    ];
    const payload = {
      ...SCENARIOS.false_completion,
      intent: intents[Math.floor(Math.random() * intents.length)],
    };
    submit(payload, e.currentTarget);
  });

  document.querySelectorAll("[data-sim]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const key = e.currentTarget.dataset.sim;
      const payload = SCENARIOS[key];
      if (payload) submit(payload, e.currentTarget);
    });
  });

  el("btn-clear").addEventListener("click", () => {
    el("terminal").innerHTML =
      '<div class="term-line term-boot"><span class="t">[clear]</span> display cleared · chain untouched</div>';
    booted = true;
  });

  // -- falsification modal ------------------------------------------------
  el("fs-close").addEventListener("click", closeFalsify);
  el("fs-cancel").addEventListener("click", closeFalsify);
  el("fs-submit").addEventListener("click", submitFalsify);

  // Clicking the backdrop closes it, clicking inside does not. Without the
  // stopPropagation a click on the textarea would dismiss a half-written
  // evidence paragraph.
  el("falsify-backdrop").addEventListener("click", (e) => {
    if (e.target === el("falsify-backdrop")) closeFalsify();
  });
  document.querySelectorAll('input[name="fs-reason"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      const isMismatch =
        document.querySelector('input[name="fs-reason"]:checked').value === "hash_mismatch";
      el("fs-observed-wrap").hidden = !isMismatch;
      if (isMismatch) el("fs-observed").focus();
    });
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !el("falsify-backdrop").hidden) closeFalsify();
  });

  const saveKey = () => {
    const value = el("api-key").value.trim();
    apiKey = value;
    keyRejected = false;  // a new key gets its own verdict from the server
    if (value) sessionStorage.setItem(KEY_STORAGE, value);
    else sessionStorage.removeItem(KEY_STORAGE);
    paintAuth(authEnabled);
    el("api-key").value = "";
    if (value) toast("已保存 key：仅本次标签页有效", "pass");
  };

  el("btn-save-key").addEventListener("click", saveKey);
  el("api-key").addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveKey();
  });

  // A key restored from sessionStorage is not echoed back into the field: the
  // input stays empty so shoulder-surfing the demo does not reveal it.
  el("api-key").placeholder = apiKey
    ? "已保存（重新输入可覆盖）"
    : "粘贴服务器上的 CEL_API_KEY";

  tick();
  setInterval(tick, POLL_MS);
});
