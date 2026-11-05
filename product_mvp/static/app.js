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

const el = (id) => document.getElementById(id);

let lastSeq = 0;
let polling = false;
let booted = false;

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
  el("m-verdicts").textContent = `PASS ${v.pass ?? 0} · FAIL ${v.fail ?? 0} · INVALID ${v.invalid ?? 0}`;

  el("chain-head").textContent = shortHash(s.chain_head);
  el("uptime").textContent = "uptime " + Math.round(s.uptime_seconds ?? 0) + "s";
  el("f-version").textContent = "core " + (s.core_version || "—");

  const pill = el("health-pill");
  const state = String(s.health_state || "HEALTHY").toLowerCase();
  pill.className = "pill pill-" + state;
  el("health-text").textContent = (s.health_state || "HEALTHY");

  paintAgents(s.agents || []);
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

function renderEntry(entry, isNew) {
  const term = el("terminal");
  const line = document.createElement("div");
  const verdict = entry.verdict || "?";
  line.className = "term-line is-" + verdict.toLowerCase();

  const trust = entry.trust_after === null || entry.trust_after === undefined
    ? "—" : fmt(entry.trust_after, 1);

  const detail =
    verdict === "FAIL" ? `<span class="term-tail">[${entry.charged_as || "FAIL"} · trust ${trust}]</span>`
    : verdict === "REFUSED" ? `<span class="term-tail">[quarantined]</span>`
    : verdict === "UNLOCK" ? `<span class="term-tail">[manual release]</span>`
    : verdict === "INVALID" ? `<span class="term-tail">[no witness submitted]</span>`
    : `<span class="term-tail">[trust ${trust}]</span>`;

  line.innerHTML = `
    <span class="seq">#${String(entry.seq).padStart(3, "0")}</span>
    <span class="t">${clockOf(entry.ts)}</span>
    <span class="v-tag v-${verdict.toLowerCase()}">${verdict}</span>
    <span class="term-agent">${entry.agent_id}</span>
    <span class="term-intent">${entry.intent}</span>
    ${detail}
    ${isNew ? '<span class="mark-new">NEW</span>' : ""}
  `;

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
    artefact: "demo://vial/42",
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
  },
};

async function submit(payload, button) {
  if (button) button.disabled = true;
  try {
    const res = await fetch("/api/v1/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

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
  try {
    const res = await fetch(`/api/v1/agents/${encodeURIComponent(agentId)}/unlock`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "operator release from dashboard" }),
    });
    const data = await res.json();
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

  tick();
  setInterval(tick, POLL_MS);
});
