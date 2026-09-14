/* Household screens: members -> home -> session. Vanilla JS, no build. Everything shown comes from the API. */
(() => {
  const $ = (id) => document.getElementById(id);
  const Q = window.HouseholdQueue;
  const P = window.HouseholdPip;
  const el = Q.el;
  const LANGUAGE_NAMES = { en: "English", es: "Español", fr: "Français" };
  const MEMBER_ACCENTS = ["--m-coral", "--m-blue", "--m-teal", "--m-plum", "--m-amber"];
  const state = { meta: null, household: null, fixtures: [], member: null, token: null, fixtureId: null, upload: null, generation: 0, request: null, session: null };
  if (P) P.renderAll();

  // Context shared with queue.js

  const ctx = {
    member: (id) => (state.household ? state.household.members.find((m) => m.id === id) : null) || null,
    name: (id) => { const m = ctx.member(id); return m ? m.name : id === "agent" ? "the agent" : id || "—"; },
    railName: (id) => { const rail = state.meta && state.meta.rails.items.find((r) => r.id === id); return rail ? rail.name : id; },
    skillName: (id) => { const skill = state.meta && state.meta.skills.find((s) => s.id === id); return skill ? skill.name : id || "—"; },
    onDecided: async (actionId, data) => {
      if (state.session) state.session.decided[actionId] = data;
      await loadHousehold();
      if (state.session) renderOutcome();
    },
  };

  // Screens

  function show(step) {
    ["members", "home", "session"].forEach((s) => { $("screen-" + s).hidden = s !== step; });
    document.body.dataset.screen = step;
    window.scrollTo({ top: 0, behavior: "instant" });
    if (step === "home") activateHomeTab("ask");
    $("switch-member").hidden = !state.member;
    $("who-line").hidden = !state.member;
    const order = ["members", "home", "session"];
    document.querySelectorAll("#steps li").forEach((li) => {
      li.classList.toggle("current", li.dataset.step === step);
      li.classList.toggle("done", order.indexOf(li.dataset.step) < order.indexOf(step));
    });
  }

  // Home tabs: one section (Ledger / Ask / Queue / Receipts) is visible at a time; Ask is the default on entering home.
  const HOME_TABS = ["ledger", "ask", "queue", "receipts"];
  function activateHomeTab(name) {
    if (!HOME_TABS.includes(name)) name = "ask";
    HOME_TABS.forEach((t) => {
      const btn = $("tab-" + t), panel = $("panel-" + t);
      if (btn) btn.setAttribute("aria-selected", String(t === name));
      if (panel) panel.hidden = t !== name;
    });
  }
  document.querySelectorAll(".home-tab").forEach((btn) => {
    btn.addEventListener("click", () => activateHomeTab(btn.dataset.tab));
  });

  // Meta and the "What is real" panel

  async function loadMeta() {
    const response = await fetch("/api/meta");
    if (!response.ok) throw new Error("The household service is unavailable. Reload to try again.");
    state.meta = await response.json();
    const m = state.meta;
    $("provider-line").textContent = "";
    $("provider-line").hidden = true;
    $("rail-provider").textContent = m.sdk;
    const roster = $("roster"); roster.replaceChildren();
    m.roster.forEach((a) => {
      const li = el("li", "agent"); li.id = "agent-" + a.id;
      li.appendChild(el("span", "dot"));
      const body = el("div");
      body.appendChild(el("div", "name", a.name));
      body.appendChild(el("div", "job", a.job));
      body.appendChild(el("div", "state", ""));
      li.appendChild(body);
      roster.appendChild(li);
    });
    $("upload-note").textContent = m.upload.available
      ? `Photos (${m.upload.kinds.filter((k) => k !== "pdf").join(", ")}) and PDFs up to ${Math.round(m.upload.max_bytes / 1048576)} MB are read by the intake agent.`
      : "Photos and PDFs can be uploaded; reading them arrives with the vision packet, so type the request for now.";
  }

  // Household ledger

  async function loadHousehold() {
    const response = await fetch("/api/household");
    if (!response.ok) throw new Error("Could not load the household ledger.");
    state.household = await response.json();
    renderMembers();
    renderLedger();
    Q.renderQueue($("queue"), state.household.actions, ctx);
    Q.renderReceipts($("receipts"), state.household.receipts.map((r) => withAction(r)), ctx);
    updateHomeGreeting();
  }

  // Pip greets the identified member by name and reflects the household's current state.
  function updateHomeGreeting() {
    if (!state.member || !state.household || !P) return;
    const first = state.member.name.split(" ")[0];
    const pending = state.household.actions.filter((a) => a.status === "needs-approval").length;
    const done = state.household.receipts.length;
    $("home-greeting-text").textContent = `Hi ${first}! I'm Pip. Tell me what you need below.`;
    $("home-greeting-sub").textContent = pending
      ? `${pending} thing${pending === 1 ? "" : "s"} waiting on a grown-up · ${done} receipt${done === 1 ? "" : "s"} so far`
      : done ? `${done} receipt${done === 1 ? "" : "s"} so far — ask me for the next thing.`
        : "Ready when you are.";
    P.setMood($("pip-home"), pending ? "waiting" : done ? "done" : "idle");
  }

  // Pip's session face mirrors the worst honesty label on screen; waiting wins when a grown-up is still needed.
  function updateSessionPip() {
    const s = state.session; if (!s || !P) return;
    const pending = s.approvals.filter((a) => !s.decided[a.action_id]).length;
    const modes = Object.values(s.receipts).map((r) => r.mode)
      .concat(Object.values(s.decided).filter((d) => d.receipt).map((d) => d.receipt.mode));
    let mood, speech;
    if (!s.result && !modes.length) { mood = "thinking"; speech = "Working on it…"; }
    else if (pending) { mood = "waiting"; speech = "One thing needs a grown-up — I set it aside for a PIN."; }
    else if (modes.length) { mood = P.moodFromModes(modes, false); speech = "All done — here is your receipt."; }
    else { mood = "idle"; speech = "All set."; }
    P.setMood($("pip-session"), mood);
    $("pip-session-speech").textContent = speech;
  }

  function withAction(receipt) {
    const action = state.household.actions.find((a) => a.id === receipt.action_id);
    return action ? { ...receipt, action_type: action.action_type, amount: action.amount, currency: action.currency, subject_member_id: action.subject_member_id } : receipt;
  }

  function memberMeta(m) {
    const meta = el("span", "meta");
    meta.appendChild(el("span", `badge ${m.role}`, m.role));
    meta.appendChild(el("span", "", LANGUAGE_NAMES[m.language] || m.language));
    if (m.guardians.length) meta.appendChild(el("span", "", `guardians: ${m.guardians.map(ctx.name).join(", ")}`));
    return meta;
  }

  function renderMembers() {
    const h = state.household;
    $("household-name").textContent = h.name;
    const grid = $("member-grid"); grid.replaceChildren();
    h.members.forEach((m, i) => {
      const card = el("button", "member-card"); card.type = "button"; card.dataset.memberId = m.id; card.dataset.role = m.role;
      card.style.setProperty("--m", `var(${MEMBER_ACCENTS[i % MEMBER_ACCENTS.length]})`);
      card.appendChild(el("span", "avatar", (m.name.trim()[0] || "?").toUpperCase()));
      card.appendChild(el("span", "name", m.name));
      card.appendChild(memberMeta(m));
      card.appendChild(el("span", "note", m.role === "minor" ? "Asks for themself; a guardian approves what is above their allowance rule." : "Decides for themself and approves for the children."));
      card.onclick = () => choose(m);
      grid.appendChild(card);
    });
  }

  function choose(m) {
    document.querySelectorAll(".member-card").forEach((c) => c.classList.toggle("selected", c.dataset.memberId === m.id));
    const form = $("pin-form"); form.hidden = false; form.dataset.memberId = m.id;
    $("pin-for").textContent = `${m.name}, enter your PIN`;
    const hint = state.household.demo_pins && state.household.demo_pins[m.id];
    $("pin-hint").textContent = hint ? `Demo household: ${m.name.split(" ")[0]}'s PIN is ${hint}` : "";
    $("pin-error").textContent = ""; $("pin").value = ""; $("pin").focus();
  }

  function ledgerRow(title, right, subParts) {
    const row = el("div", "ledger-row");
    row.appendChild(el("span", "title", title));
    const rightNode = el("span", "right"); if (right instanceof Node) rightNode.appendChild(right); else rightNode.textContent = right || "";
    row.appendChild(rightNode);
    const sub = el("span", "sub");
    subParts.forEach((part) => { if (part instanceof Node) sub.appendChild(part); else if (part) sub.appendChild(document.createTextNode(part + " ")); });
    row.appendChild(sub);
    return row;
  }

  function renderLedger() {
    const h = state.household;
    const members = $("ledger-members"); members.replaceChildren();
    h.members.forEach((m) => {
      const row = ledgerRow(m.name, el("span", `badge ${m.role}`, m.role), [LANGUAGE_NAMES[m.language] || m.language, m.guardians.length ? `· guardians ${m.guardians.map(ctx.name).join(", ")}` : "", m.email ? `· ${m.email}` : ""]);
      row.dataset.memberId = m.id; members.appendChild(row);
    });
    const grants = $("ledger-grants"); grants.replaceChildren();
    h.grants.forEach((g) => {
      const limit = g.limit_amount ? `up to ${g.limit_amount} ${g.limit_currency} ${g.limit_period}` : "no amount limit";
      const scope = el("span"); g.scope.forEach((s) => scope.appendChild(el("span", "chip", s)));
      const row = ledgerRow(`${ctx.name(g.grantor_id)} lets ${ctx.name(g.grantee_id)} decide for ${ctx.name(g.subject_id)}`, el("span", `chip state-${g.state}`, g.state), [scope, `${limit} · ${g.basis} · until ${Q.when(g.expires_at)}`, g.consent_id ? `· consent ${g.consent_id}` : "· no consent record"]);
      row.dataset.grantId = g.id; row.dataset.state = g.state; grants.appendChild(row);
    });
    const consents = $("ledger-consents"); consents.replaceChildren();
    if (!h.consents.length) consents.appendChild(el("p", "empty", "No consents recorded."));
    h.consents.forEach((c) => {
      const row = ledgerRow(`${ctx.name(c.member_id)} · ${c.kind}`, Q.when(c.at), [`for ${c.target_id} · via ${c.channel}`]);
      row.dataset.consentId = c.id; row.dataset.kind = c.kind; consents.appendChild(row);
    });
    const accounts = $("ledger-accounts"); accounts.replaceChildren();
    h.accounts.forEach((a) => {
      const rules = Object.entries(a.rules || {}).map(([k, v]) => `${k.replace("_", " ")} ${v}`).join(" · ");
      const row = ledgerRow(`${ctx.name(a.owner_member_id)} · ${a.kind}`, `${a.balance} ${a.currency}`, [a.id, rules ? `· ${rules}` : ""]);
      row.dataset.accountId = a.id; row.dataset.balance = a.balance; accounts.appendChild(row);
    });
  }

  // Identify

  $("pin-form").onsubmit = async (event) => {
    event.preventDefault();
    const memberId = $("pin-form").dataset.memberId;
    $("pin-error").textContent = "";
    try {
      const data = await Q.post("/api/identify", { member_id: memberId, pin: $("pin").value });
      state.member = data.member; state.token = data.session_token; state.pin = $("pin").value;
      $("pin").value = "";
      const who = $("who-line"); who.replaceChildren(el("span", "", data.member.name), el("span", `badge ${data.member.role}`, data.member.role));
      $("ask-as").textContent = `Speaking as ${data.member.name} · ${data.member.role} · ${LANGUAGE_NAMES[data.member.language] || data.member.language}`;
      await loadFixtures();
      await loadHousehold();
      show("home");
    } catch (err) { $("pin-error").textContent = err.message; }
  };
  $("pin-cancel").onclick = () => { $("pin-form").hidden = true; document.querySelectorAll(".member-card").forEach((c) => c.classList.remove("selected")); };

  // Voice (P7): the same member, identified again by PIN over the socket; every tool call runs under the authority hook
  let voice = null;
  function say(role, text) {
    if (!text) return;
    const item = el("li", "", ""); item.append(el("b", "", role), document.createTextNode(text));
    $("voice-transcript").append(item); $("voice-transcript").scrollTop = $("voice-transcript").scrollHeight;
  }
  function onVoiceFrame(frame) {
    if (frame.type === "identified") { $("voice-banner").textContent = frame.banner || `Voice: ${frame.mode}`; $("voice-text-form").hidden = frame.mode !== "text"; }
    if (frame.type === "fallback") { $("voice-banner").textContent = frame.banner || "Voice unavailable: text fallback"; $("voice-text-form").hidden = false; }
    if (frame.type === "bidi_transcript_stream" && frame.is_final) say(frame.role === "user" ? "you" : "agent", frame.text);
    if (frame.type === "tool") {
      if (frame.status === "done" || frame.status === "vetoed") { say("agent", frame.say); loadHousehold().catch(() => {}); }
      if (frame.status === "error") say("agent", frame.say || "That did not work.");
    }
    if (frame.type === "identify_failed") $("ask-error").textContent = `Voice: identification failed (${frame.attempts_left} attempts left)`;
    if (frame.type === "error") $("ask-error").textContent = `Voice: ${frame.detail || frame.code || "error"}`;
    if (frame.type === "closed") stopVoice();
  }
  function stopVoice() {
    if (voice) { try { voice.stop(); } catch (_) { /* already closed */ } }
    voice = null; $("talk").textContent = "Talk instead"; $("voice-text-form").hidden = true;
  }
  $("talk").onclick = async () => {
    if (voice) { stopVoice(); return; }
    if (!state.member || !state.pin) { $("ask-error").textContent = "Identify with your PIN first."; return; }
    $("ask-error").textContent = ""; $("voice-panel").hidden = false; $("voice-transcript").replaceChildren();
    $("talk").textContent = "Stop talking"; $("voice-banner").textContent = "Connecting…";
    try {
      const { HouseholdVoice } = await import("/static/voice/voice.js");
      voice = new HouseholdVoice({ memberId: state.member.id, pin: state.pin, onFrame: onVoiceFrame });
      const identified = await voice.connect();
      if (identified.mode === "sonic") await voice.startMic();
    } catch (err) { $("ask-error").textContent = `Voice: ${err && err.message ? err.message : err}`; stopVoice(); }
  };
  $("voice-text-form").onsubmit = (event) => {
    event.preventDefault();
    const text = $("voice-text").value.trim();
    if (!voice || !text) return;
    say("you", text); voice.sendText(text); $("voice-text").value = "";
  };

  function switchMember() {
    stopVoice(); $("voice-panel").hidden = true; state.pin = null;
    resetSession();
    state.member = null; state.token = null; state.fixtureId = null; state.upload = null;
    $("request-text").value = ""; $("upload-status").textContent = ""; $("upload-clear").hidden = true; $("ask-error").textContent = "";
    $("pin-form").hidden = true;
    document.querySelectorAll(".member-card").forEach((c) => c.classList.remove("selected"));
    show("members");
    loadHousehold().catch((err) => { $("startup-error").textContent = err.message; $("service-unavailable").hidden = false; });
  }
  $("switch-member").onclick = switchMember;

  // Ask

  async function loadFixtures() {
    const response = await fetch("/api/fixtures");
    state.fixtures = response.ok ? await response.json() : [];
    const list = $("sample-list"); list.replaceChildren();
    const mine = state.fixtures.filter((f) => f.actor_member_id === state.member.id);
    if (!mine.length) list.appendChild(el("p", "empty", "No sample requests are written for this member."));
    mine.forEach((f) => {
      const b = el("button", "sample"); b.type = "button"; b.dataset.fixtureId = f.id;
      b.appendChild(el("span", "text", f.request));
      b.appendChild(el("span", "tags", f.tags.join(" · ")));
      b.onclick = () => { $("request-text").value = f.request; state.fixtureId = f.id; clearUpload(); $("ask-error").textContent = ""; };
      list.appendChild(b);
    });
  }

  $("request-text").oninput = () => { state.fixtureId = null; };

  function clearUpload() { state.upload = null; $("upload-status").textContent = ""; $("upload-clear").hidden = true; $("upload-file").value = ""; }
  $("upload-clear").onclick = clearUpload;
  $("upload-file").onchange = async () => {
    const file = $("upload-file").files[0];
    if (!file) return;
    $("ask-error").textContent = ""; $("upload-status").textContent = `Uploading ${file.name}…`;
    const body = new FormData(); body.append("file", file); body.append("session_token", state.token);
    try {
      const response = await fetch("/api/intake", { method: "POST", body });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Upload failed.");
      state.upload = data;
      $("upload-status").textContent = `Uploaded ${data.filename} (${data.kind}, ${Math.max(1, Math.round(data.size / 1024))} KB)`;
      $("upload-clear").hidden = false;
      $("request-text").value = ""; state.fixtureId = null;
    } catch (err) { clearUpload(); $("ask-error").textContent = err.message; }
  };

  $("run-request").onclick = () => {
    const text = $("request-text").value.trim();
    $("ask-error").textContent = "";
    if (state.upload) {
      if (!state.meta.upload.available) { $("ask-error").textContent = $("upload-note").textContent; return; }
      startSession({ upload_id: state.upload.upload_id }, `Uploaded ${state.upload.filename}`);
      return;
    }
    if (!text) { $("ask-error").textContent = "Type what you need, pick a sample request, or upload a document."; return; }
    const fixture = state.fixtureId && state.fixtures.find((f) => f.id === state.fixtureId && f.request === text);
    startSession(fixture ? { fixture_id: fixture.id } : { request_text: text }, text);
  };

  // Session

  function resetSession() {
    state.generation += 1;
    if (state.request) state.request.abort();
    state.request = null;
    state.session = null;
    ["session-error", "session-sub", "request-who", "request-text-view", "intake-body", "matcher-body", "plan-body", "executor-body", "briefing-headline", "briefing-headline-en", "briefing-next", "guard-text"].forEach((id) => { $(id).textContent = ""; });
    ["intake-card", "matcher-card", "plan-card", "executor-card", "briefing-card", "approvals-card", "session-receipts-card", "guard-card", "outcome-chip", "session-error", "briefing-done-row", "briefing-waiting-row"].forEach((id) => { $(id).hidden = true; });
    ["approvals", "session-receipts", "briefing-done", "briefing-waiting", "briefing-labels", "guard-notes"].forEach((id) => { $(id).replaceChildren(); });
    $("outcome-chip").className = "chip outcome";
    document.querySelectorAll(".agent").forEach((li) => { li.className = "agent"; const st = li.querySelector(".state"); st.textContent = ""; st.className = "state"; });
    if (P) { P.setMood($("pip-session"), "thinking"); $("pip-session-speech").textContent = "Working on it…"; }
  }

  async function startSession(body, title) {
    if (!state.member || !state.token) { show("members"); return; }
    resetSession();
    state.session = { events: [], actions: {}, receipts: {}, approvals: [], decided: {}, result: null, start: null };
    $("request-who").textContent = `${state.member.name} (${state.member.role}) asked`;
    $("request-text-view").textContent = title;
    $("member-pane-title").textContent = `For ${state.member.name.split(" ")[0]}`;
    show("session");
    const generation = state.generation;
    const controller = new AbortController(); state.request = controller;
    try {
      const res = await fetch("/api/run", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ...body, actor_member_id: state.member.id, session_token: state.token }), signal: controller.signal });
      if (!res.ok || !res.body) {
        let detail = `Server returned ${res.status}.`;
        try { const data = await res.json(); if (typeof data.detail === "string") detail = data.detail; } catch { /* keep the status line */ }
        if (res.status === 401) { showError(detail); switchMember(); return; }
        throw new Error(detail);
      }
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (generation !== state.generation) { await reader.cancel(); return; }
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
          const line = chunk.split("\n").find((l) => l.startsWith("data: ")); if (!line) continue;
          const ev = JSON.parse(line.slice(6));
          if (ev.event === "error") throw Object.assign(new Error(ev.detail), { code: ev.code });
          handle(ev);
        }
      }
      if (!state.session || !state.session.result) throw new Error("The connection ended before a result arrived.");
      await loadHousehold();
    } catch (error) {
      if (generation === state.generation && error.name !== "AbortError") showError(error.message);
    } finally {
      if (generation === state.generation) state.request = null;
    }
  }

  function showError(detail) {
    $("session-error").textContent = `The session did not finish. ${detail}`;
    $("session-error").hidden = false;
    document.querySelectorAll(".agent.running").forEach((li) => { li.classList.remove("running"); li.querySelector(".state").textContent = "stopped"; });
  }

  function handle(ev) {
    const s = state.session; if (!s) return;
    s.events.push(ev);
    if (ev.event === "session_start") {
      s.start = ev;
      $("request-who").textContent = `${ev.actor_name} (${ctx.member(ev.actor_member_id)?.role || ""}) asked · ${ev.request_id === "adhoc" ? "typed request" : "sample request " + ev.request_id}`;
      $("request-text-view").textContent = ev.request_text;
      $("session-sub").textContent = "";
    } else if (ev.event === "node_start") {
      const li = $("agent-" + ev.node_id); li.classList.remove("done"); li.classList.add("running");
      li.querySelector(".state").textContent = ev.run > 1 ? `running · run ${ev.run}` : "running";
    } else if (ev.event === "node_done") {
      const li = $("agent-" + ev.node_id); li.classList.remove("running"); li.classList.add("done");
      const st = li.querySelector(".state"); st.textContent = ev.status;
      if (ev.node_id === "intake" && ev.output) renderIntake(ev.output);
      if (ev.node_id === "matcher" && ev.output) renderMatcher(ev.output);
      if (ev.node_id === "planner" && ev.output) renderPlanNode(ev.output, ev.run);
      if (ev.node_id === "authority" && ev.output) renderVerdict(ev.output, ev.run, li);
      if (ev.node_id === "executor" && ev.output) renderExecutorNode(ev.output);
      if (ev.node_id === "briefer" && ev.output) renderBriefing(ev.output);
    } else if (ev.event === "action") {
      s.actions[ev.proposal.id] = ev;
      renderAction(ev);
    } else if (ev.event === "approval_needed") {
      s.approvals.push(ev);
      renderApproval(ev);
    } else if (ev.event === "receipt") {
      s.receipts[ev.receipt.action_id] = ev.receipt;
      renderReceipt(ev.receipt);
    } else if (ev.event === "result") {
      s.result = ev.result;
      state.meta.roster.forEach((a) => { if (!ev.result.execution_order.includes(a.id)) { const li = $("agent-" + a.id); li.classList.add("skipped"); li.querySelector(".state").textContent = "not needed this time"; } });
      if (ev.result.briefing) renderBriefing(ev.result.briefing);
      renderGuard(ev.result);
      renderOutcome();
    }
  }

  function kv(pairs) {
    const dl = el("dl", "kv");
    pairs.forEach(([k, v]) => { if (v === undefined || v === null || v === "") return; dl.appendChild(el("dt", "", k)); dl.appendChild(el("dd", "", v)); });
    return dl;
  }

  function renderIntake(r) {
    const body = $("intake-body"); body.replaceChildren();
    const chips = el("div", "chips");
    chips.appendChild(el("span", "chip", r.document_class));
    chips.appendChild(el("span", "chip", `confidence ${r.confidence}`));
    body.appendChild(chips);
    body.appendChild(el("p", "", r.summary_en));
    body.appendChild(kv([["From", r.issuer], ["About", r.subject_hint], ...(r.amounts || []).map((a) => [a.label, a.amount_text]), ...(r.dates || []).map((d) => [d.label, d.date_text])]));
    (r.evidence || []).slice(0, 2).forEach((q) => body.appendChild(el("p", "quote", `“${q}”`)));
    $("intake-card").hidden = false;
  }

  function renderMatcher(a) {
    const body = $("matcher-body"); body.replaceChildren();
    body.appendChild(kv([["About", ctx.name(a.subject_member_id)], ["Asked by", ctx.name(a.actor_member_id)], ["Skill", ctx.skillName(a.skill_id)], ["Account", a.account_id], ["Confidence", a.confidence]]));
    if (a.reasons && a.reasons.length) { const ul = el("ul", "line-list"); a.reasons.forEach((r) => ul.appendChild(el("li", "", r))); body.appendChild(ul); }
    $("matcher-card").hidden = false;
  }

  function revisionBlock(run) {
    let block = document.querySelector(`#plan-body .plan-revision[data-revision="${run}"]`);
    if (!block) {
      block = el("div", "plan-revision"); block.dataset.revision = String(run);
      block.appendChild(el("div", "rev", run > 1 ? `Plan revision ${run} (sent back by the authority)` : "Plan revision 1"));
      $("plan-body").appendChild(block);
    }
    return block;
  }

  function renderPlanNode(plan, run) {
    const block = revisionBlock(run);
    let note = block.querySelector(".plan-note");
    if (!note) { note = el("div", "why plan-note"); block.appendChild(note); }
    const parts = [`${plan.actions.length} action${plan.actions.length === 1 ? "" : "s"} proposed`];
    if (plan.needs && plan.needs.length) parts.push(`needs: ${plan.needs.join("; ")}`);
    if (plan.notes && plan.notes.length) parts.push(plan.notes.join(" "));
    note.textContent = parts.join(" · ");
    $("plan-card").hidden = false;
  }

  function renderAction(ev) {
    const block = revisionBlock(ev.revision);
    const p = ev.proposal, d = ev.decision;
    const card = el("div", "action"); card.dataset.actionId = p.id; card.dataset.outcome = d.outcome;
    const words = Q.describe({ ...p }, ctx);
    const head = el("div", "head");
    head.appendChild(el("span", "title", words.title));
    head.appendChild(Q.outcomeChip(d.outcome));
    card.appendChild(head);
    card.appendChild(el("div", "why", `${words.sub}${p.claimed_grant_id ? " · cites " + p.claimed_grant_id : ""}`));
    if (p.rationale) card.appendChild(el("div", "why", p.rationale));
    card.appendChild(el("div", "explain", ev.explanation));
    block.appendChild(card);
    $("plan-card").hidden = false;
  }

  function renderVerdict(v, run, li) {
    const block = revisionBlock(run);
    let line = block.querySelector(".verdict-line");
    if (!line) { line = el("div", "why verdict-line"); block.appendChild(line); }
    line.textContent = `Authority verdict: ${v.verdict}`;
    const st = li.querySelector(".state");
    if (v.verdict === "revise") { st.textContent = "sent the plan back"; st.className = "state bad"; li.classList.add("rejected"); }
    else if (v.verdict === "stop") { st.textContent = "stopped: nothing may proceed"; st.className = "state bad"; }
    else { st.textContent = "proceed"; st.className = "state good"; }
  }

  function renderExecutorNode(report) {
    const body = $("executor-body");
    let note = body.querySelector(".exec-note");
    if (!note) { note = el("p", "why exec-note"); body.appendChild(note); }
    note.textContent = `${report.receipts.length} receipt${report.receipts.length === 1 ? "" : "s"} issued by execute_action` + (report.skipped.length ? ` · skipped ${report.skipped.length}` : "");
    $("executor-card").hidden = false;
  }

  function renderReceipt(receipt) {
    const action = state.session.actions[receipt.action_id];
    const enriched = action ? { ...receipt, action_type: action.proposal.action_type, amount: action.proposal.amount, currency: action.proposal.currency, subject_member_id: action.proposal.subject_member_id } : receipt;
    $("executor-body").appendChild(Q.receiptRow(enriched, ctx));
    $("session-receipts").appendChild(Q.receiptRow(enriched, ctx));
    $("executor-card").hidden = false; $("session-receipts-card").hidden = false;
    updateSessionPip();
  }

  function renderApproval(ev) {
    const action = state.session.actions[ev.action_id];
    const box = el("div", "action approval"); box.dataset.actionId = ev.action_id;
    const words = action ? Q.describe(action.proposal, ctx) : { title: ev.action_id, sub: "" };
    const head = el("div", "head");
    head.appendChild(el("span", "title", words.title));
    head.appendChild(Q.outcomeChip("needs-approval"));
    box.appendChild(head);
    box.appendChild(el("div", "why", `${words.sub} · needs ${ev.approver_ids.map(ctx.name).join(" or ")}`));
    box.appendChild(el("div", "explain", ev.explanation));
    box.appendChild(Q.approvalForm({ id: ev.action_id, approver_ids: ev.approver_ids }, ctx));
    $("approvals").appendChild(box);
    $("approvals-card").hidden = false;
  }

  function renderBriefing(b) {
    const lang = state.session && state.session.start ? state.session.start.language : state.member.language;
    $("briefing-headline").textContent = b.headline_target; $("briefing-headline").lang = lang;
    $("briefing-headline-en").textContent = b.headline_en !== b.headline_target ? b.headline_en : "";
    const done = $("briefing-done"); done.replaceChildren(); (b.done || []).forEach((line) => done.appendChild(el("li", "", line)));
    $("briefing-done-row").hidden = !(b.done && b.done.length);
    const waiting = $("briefing-waiting"); waiting.replaceChildren(); (b.waiting_on || []).forEach((line) => waiting.appendChild(el("li", "", line)));
    $("briefing-waiting-row").hidden = !(b.waiting_on && b.waiting_on.length);
    $("briefing-next").textContent = b.next_step_target + (b.next_step_en !== b.next_step_target ? ` (${b.next_step_en})` : "");
    $("briefing-next").lang = lang;
    const labels = $("briefing-labels"); labels.replaceChildren();
    $("briefing-card").hidden = false;
    updateSessionPip();
  }

  function renderGuard(result) {
    const g = result.guard;
    $("guard-text").textContent = "Every decision here was checked in code before anything moved.";
    const notes = $("guard-notes"); notes.replaceChildren();
    $("guard-card").hidden = false;
  }

  function renderOutcome() {
    const s = state.session; if (!s || !s.result) return;
    const pending = s.approvals.filter((a) => !s.decided[a.action_id]).length;
    const approvedReceipts = Object.values(s.decided).filter((d) => d.receipt).length;
    let outcome = s.result.outcome;
    if (s.approvals.length && !pending) outcome = approvedReceipts || Object.keys(s.receipts).length ? "executed" : "declined";
    else if (approvedReceipts && pending) outcome = "partial";
    const chip = $("outcome-chip");
    chip.className = `chip outcome outcome-${outcome === "executed" ? "allow" : outcome === "needs-approval" || outcome === "partial" ? "needs-approval" : outcome === "no-action" ? "none" : "block"}`;
    chip.textContent = { executed: "done", "needs-approval": "waiting for approval", partial: "partly done, waiting for approval", blocked: "blocked", "no-action": "nothing to do", declined: "declined" }[outcome] || outcome;
    chip.hidden = false;
    // Once a grown-up has decided every pending approval, the "waiting" briefing is stale — settle it honestly.
    // The exact amount, approver name and label live in the receipt below; this only clears the contradiction.
    if (s.approvals.length && !pending && !$("briefing-card").hidden) {
      const bh = $("briefing-headline");
      if (outcome === "executed" || outcome === "partial") {
        bh.textContent = "Approved — the receipt is below."; bh.lang = "en";
        $("briefing-next").textContent = "The receipt below is the proof; nothing else is waiting.";
      } else if (outcome === "declined") {
        bh.textContent = "Declined — nothing moved."; bh.lang = "en";
        $("briefing-next").textContent = "The decline is recorded in the ledger.";
      }
      $("briefing-headline-en").textContent = "";
      $("briefing-waiting-row").hidden = true;
    }
    updateSessionPip();
  }

  $("back-home").onclick = async () => { resetSession(); await loadHousehold(); show("home"); };
  $("reload-service").onclick = () => window.location.reload();

  loadMeta().then(loadHousehold).then(() => show("members")).catch((error) => {
    $("provider-line").hidden = false; $("provider-line").textContent = error.message;
    $("startup-error").textContent = error.message;
    $("service-unavailable").hidden = false;
  });
})();
