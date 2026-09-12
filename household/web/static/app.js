/* Front Desk screen: language -> consent -> document -> two-faced session. Vanilla JS, no build. */
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { meta: null, language: "es", fixture: null, docTitle: "", privacy: false, result: null, drafts: [], request: null, generation: 0, consented: false, handoff: null, interpretationText: "" };
  const LABELS = {
    es: { who: "Quién", next: "Siguiente paso", when: "Cuándo", safe: "Lo seguro hoy", visitor: "Visitante", readAloud: "Leer en voz alta", consent: "Un programa leerá el texto de su carta. Esta aplicación no guarda la carta ni la conversación en un archivo. Al terminar la visita, borramos el texto de esta pantalla. Puede imprimir un resumen y, si se aprueba, un borrador de respuesta. Los avisos legales requieren ayuda de una persona. Puede elegir no usar el programa.", agree: "Estoy de acuerdo", decline: "Hoy no", reply: "Borrador para revisar con el personal", print: "Imprimir resumen y respuesta", printSummary: "Imprimir resumen" },
    en: { who: "Who", next: "Next", when: "When", safe: "Safe today", visitor: "Visitor", readAloud: "Read aloud", consent: "", agree: "I agree", decline: "Not today", reply: "Draft to review with staff", print: "Print summary and reply", printSummary: "Print summary" },
  };
  const labels = () => LABELS[state.language] || LABELS.en;

  function show(step) {
    ["language", "consent", "document", "session"].forEach((s) => { $("screen-" + s).hidden = s !== step; });
    document.body.classList.toggle("in-session", step === "session");
    document.body.dataset.screen = step;
    window.scrollTo({ top: 0, behavior: "instant" });
    $("privacy-toggle").hidden = step !== "session";
    $("new-session").hidden = step !== "session";
    document.querySelectorAll("#steps li").forEach((li) => {
      const order = ["language", "consent", "document", "session"];
      li.classList.toggle("current", li.dataset.step === step);
      li.classList.toggle("done", order.indexOf(li.dataset.step) < order.indexOf(step));
    });
  }

  // Presentation only: sentence segments retain the complete original text, including whitespace.
  function presentParagraphs(element, text, perParagraph = 2) {
    element.replaceChildren();
    const sentences = typeof Intl.Segmenter === "function"
      ? Array.from(new Intl.Segmenter(state.language, { granularity: "sentence" }).segment(text), (part) => part.segment)
      : [text];
    for (let index = 0; index < sentences.length; index += perParagraph) {
      const paragraph = document.createElement("p");
      paragraph.textContent = sentences.slice(index, index + perParagraph).join("");
      element.appendChild(paragraph);
    }
  }

  function presentSourceFacts(reading) {
    const facts = $("source-facts"); facts.replaceChildren();
    // Read-only source facts; interpretation and model outputs stay intact.
    if (!reading || reading.source_issues?.length || !["en", "es"].includes(state.language)) { facts.hidden = true; return; }
    const entries = [];
    if (reading.deadlines?.length) entries.push([state.language === "es" ? "Fechas de la carta" : "Dates in the letter", reading.deadlines.map((date) => date.date_text).join(" · ")]);
    if (reading.amounts?.length) entries.push([state.language === "es" ? "Cantidades de la carta" : "Amounts in the letter", reading.amounts.join(" · ")]);
    entries.forEach(([label, value]) => {
      const row = document.createElement("div"), term = document.createElement("dt"), detail = document.createElement("dd");
      term.textContent = label; detail.textContent = value;
      row.append(term, detail); facts.appendChild(row);
    });
    facts.hidden = !entries.length;
    if (entries.length && state.interpretationText) {
      const text = state.interpretationText;
      const marker = state.language === "es" ? " Fechas: " : " Dates: ";
      const boundary = text.lastIndexOf(marker);
      const sourceValues = [...(reading.deadlines || []).map((date) => date.date_text), ...(reading.amounts || [])];
      // Only fold a known trailing annotation when every source value is present there.
      if (boundary >= 0 && sourceValues.length && sourceValues.every((value) => text.slice(boundary).includes(value))) {
        presentParagraphs($("visitor-text"), text.slice(0, boundary));
        const wrapper = document.createElement("div"), details = document.createElement("details"), summary = document.createElement("summary"), full = document.createElement("div");
        wrapper.className = "full-interpretation";
        summary.textContent = state.language === "es" ? "Interpretación completa" : "Full interpretation";
        full.className = "full-interpretation-text";
        presentParagraphs(full, text);
        details.append(summary, full); wrapper.appendChild(details); facts.appendChild(wrapper);
      }
    }
  }

  async function loadMeta() {
    const response = await fetch("/api/meta");
    if (!response.ok) throw new Error("The reading service is unavailable. Ask staff for help, then reload to try again.");
    state.meta = await response.json();
    const m = state.meta;
    const line = `${m.sdk} · ${m.provider === "fake" ? "demo mode (fake provider)" : m.provider} · ${m.model_id}`;
    $("provider-line").textContent = m.provider === "fake" ? "Fixture demonstration" : `${m.provider} · ${m.model_id}`;
    $("rail-provider").textContent = line;
    const remote = m.backend && m.backend !== "local";
    const destination = m.backend === "agentcore" ? "AWS AgentCore Runtime" : "the configured Runtime service";
    $("consent-where").textContent = remote
      ? `The letter is sent to ${destination}. ` + (m.provider === "fake" ? "It runs a fixture demonstration without calling an AI model. Service logging policies still apply; ask staff to explain them before agreeing." : `It calls a model host (${m.provider}, ${m.model_id}). The service logging and model provider retention policies apply; ask staff to explain them before agreeing.`)
      : m.provider === "fake" ? "This computer, in demo mode. No network call is made." : `A model host (${m.provider}, ${m.model_id}). The letter is sent to that provider; its retention policy also applies. Ask staff to explain that policy before agreeing.`;
    $("graph-src").textContent = m.graph_mermaid;
    const grid = $("language-grid"); grid.innerHTML = "";
    m.languages.filter((l) => l.code !== "en").forEach((l) => {
      const b = document.createElement("button"); b.className = "btn lang"; b.dataset.code = l.code;
      const voice = l.browser;
      b.innerHTML = `<span class="native" lang="${l.code}">${l.name_native}</span><span class="language-name">${l.name_en}</span><span class="language-arrow" aria-hidden="true">↗</span>`;
      b.title = voice ? "Audio depends on installed browser voice" : "Text; audio only if a matching voice is installed";
      b.onclick = () => {
        state.language = l.code; $("visitor-lang").textContent = `${labels().visitor} · ${l.name_native}`; $("read-aloud").textContent = labels().readAloud;
        state.consented = false;
        $("visitor").lang = l.code; $("visitor").dir = ["ar", "fa"].includes(l.code) ? "rtl" : "ltr";
        const consent = labels().consent;
        $("consent-target").hidden = !consent; $("consent-target").lang = l.code;
        $("consent-target").textContent = consent + (remote ? (m.provider === "fake" ? " El texto se envía al servicio Runtime para una demostración sin llamar a un modelo de inteligencia artificial. Las políticas de registro del servicio también se aplican; pida al personal que se las explique." : " El texto se envía al servicio Runtime y a un proveedor de inteligencia artificial. Sus políticas de registro y conservación también se aplican; pida al personal que se las explique.") : m.provider === "fake" ? " En esta demostración, el texto se procesa en esta computadora sin enviarlo a un modelo externo." : " El texto se envía a un proveedor de inteligencia artificial; su política de conservación también se aplica. Pida al personal que se la explique.");
        if (consent && typeof Intl.Segmenter === "function") {
          const sentences = Array.from(new Intl.Segmenter("es", { granularity: "sentence" }).segment($("consent-target").textContent), (part) => part.segment);
          const sections = [sentences.slice(0, 1).join("") + sentences.slice(6).join(""), sentences.slice(1, 4).join(""), sentences.slice(4, 6).join("")];
          $("consent-target").replaceChildren(...sections.map((text) => { const p = document.createElement("p"); p.textContent = text; return p; }));
        }
        $("consent-assist").hidden = Boolean(consent);
        $("consent-confirm").checked = false;
        $("consent-agree").disabled = !consent;
        $("consent-agree").textContent = labels().agree;
        $("consent-decline").textContent = labels().decline;
        $("consent-status").textContent = "";
        show("consent");
      };
      grid.appendChild(b);
    });
    const roster = $("roster"); roster.innerHTML = "";
    m.roster.forEach((a) => {
      const li = document.createElement("li"); li.className = "agent"; li.id = "agent-" + a.id;
      li.innerHTML = `<span class="dot"></span><div><div class="name">${a.name}</div><div class="state"></div><details class="agent-details"><summary aria-label="About ${a.name}">About this step</summary><div class="job">${a.job}</div>${a.can_reject ? '<div class="job">Can reject a draft</div>' : ""}${a.tools.length ? `<div class="tools">tools: ${a.tools.join(", ")}</div>` : ""}</details></div>`;
      roster.appendChild(li);
    });
  }

  async function loadFixtures() {
    const docs = await (await fetch("/api/fixtures")).json();
    const list = $("fixture-list"); list.innerHTML = "";
    const order = (d) => (d.tags.includes("hero") ? 0 : 1);
    docs.sort((a, b) => order(a) - order(b) || a.title.localeCompare(b.title)).forEach((d) => {
      const b = document.createElement("button"); b.className = "btn doc"; b.dataset.featured = String(d.tags.includes("hero"));
      b.innerHTML = `<span class="row1"><span class="title">${esc(d.title)}</span><span class="stakes">${d.stakes}</span></span><span class="src" title="${esc(d.source)}">${d.is_real ? "real public form" : "synthetic, labelled fictional"}${d.tags.includes("hero") ? " · demo" : ""}</span>`;
      b.onclick = () => startSession({ fixture_id: d.id, language: state.language }, d.title);
      list.appendChild(b);
    });
  }

  function resetSession() {
    state.generation += 1;
    state.request?.abort(); state.request = null;
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    state.result = null; state.drafts = []; state.handoff = null; state.interpretationText = "";
    $("handoff-form").reset(); $("handoff-editor").hidden = true; $("handoff-editor").open = false;
    $("handoff-withdraw").hidden = true; $("visitor-handoff").hidden = true; $("visitor-handoff").replaceChildren();
    ["handoff-error", "handoff-edit-status", "handoff-time-help"].forEach(id => { $(id).textContent = ""; });
    $("handoff-time-field").hidden = true; $("handoff-time").required = false;
    ["backtrans-text", "card-headline", "card-statement", "card-who", "card-next", "card-when", "card-safe", "card-rule", "approved-reply-text", "original-form-text", "original-form-heading", "original-form-note", "print-area", "meaning-warnings", "session-error", "speech-status"].forEach((id) => { $(id).textContent = ""; });
    $("approved-reply").hidden = true; $("print-summary").disabled = true;
    $("session-error").hidden = true; $("meaning-warnings").hidden = true;
    document.querySelectorAll(".agent").forEach((li) => { li.className = "agent"; li.querySelector(".state").textContent = ""; li.querySelector(".state").className = "state"; });
    $("reading-card").innerHTML = '<h2>Reading</h2><p class="muted">Waiting for the document reader…</p>';
    ["backtrans-card", "verdict-card", "draft-card", "visitor-card"].forEach((id) => { $(id).hidden = true; });
    $("verdict-log").innerHTML = ""; $("draft-log").innerHTML = ""; $("number-chips").innerHTML = "";
    $("visitor-text").textContent = "…";
    $("source-facts").replaceChildren(); $("source-facts").hidden = true;
    document.querySelectorAll(".agent-details").forEach((details) => { details.open = false; });
    setSeam(null);
  }

  function newVisitor() {
    resetSession(); state.language = "es"; state.fixture = null; state.docTitle = ""; state.consented = false;
    state.privacy = false; document.body.classList.remove("privacy");
    $("privacy-toggle").textContent = "Privacy mode";
    ["paste", "paste-title"].forEach((id) => { $(id).value = ""; });
    $("doc-chip").textContent = ""; $("visitor-lang").textContent = "Visitor";
    $("visitor").lang = ""; $("visitor").dir = "ltr";
    $("consent-target").textContent = ""; $("consent-confirm").checked = false;
    $("consent-status").textContent = ""; $("consent-agree").disabled = true;
    $("fixture-list").replaceChildren(); show("language");
  }

  function setSeam(fid) {
    const seam = $("seam"); seam.className = "seam";
    const chips = [$("gauge-chip"), $("gauge-chip-visitor")];
    if (!fid) { $("seam-fill").style.height = "0%"; chips.forEach((c) => { c.className = "gauge-chip"; c.textContent = "fidelity: waiting"; }); $("gauge-chip-visitor").hidden = true; return; }
    $("meaning-warnings").textContent = (fid.meaning_warnings || []).join(" ");
    $("meaning-warnings").hidden = !fid.meaning_warnings?.length;
    seam.classList.add(fid.band);
    $("seam-fill").style.height = Math.round(fid.score * 100) + "%";
    chips.forEach((c) => { c.className = "gauge-chip " + fid.band; c.textContent = `fidelity ${fid.score.toFixed(2)} · ${fid.band}`; c.hidden = false; });
  }

  function esc(s) { return String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

  function renderReading(r, policy) {
    $("reading-card").innerHTML = `<h2>Reading</h2>${policy ? `<p class="small">Source-checked policy explanation · ${esc(policy.rule_id)} · ${esc(policy.version)}</p>` : ""}<p class="reading-title">${esc(r.title)}</p>
      <div class="chips"><span class="chip">${esc(r.document_class)}</span><span class="chip stakes-${esc(r.stakes)}">stakes ${esc(r.stakes)}</span><span class="chip">confidence ${Number(r.confidence).toFixed(2)}</span></div>
      <p>${esc(r.what_it_is)}</p><p>${esc(r.what_it_asks)}</p>
      <dl class="kv">${(r.deadlines || []).map((d) => `<dt>${esc(d.label)}</dt><dd><strong>${esc(d.date_text)}</strong></dd>`).join("")}${r.amounts?.length ? `<dt>amounts</dt><dd><strong>${esc(r.amounts.join(", "))}</strong></dd>` : ""}</dl>
      ${(r.evidence || []).slice(0, 3).map((q) => `<p class="quote">“${esc(q)}”</p>`).join("")}`;
  }

  function renderInterpretation(i, fid) {
    state.interpretationText = i.target_text;
    presentParagraphs($("visitor-text"), i.target_text);
    $("visitor-text").classList.toggle("long", i.target_text.length > 420);
    $("backtrans-card").hidden = false;
    $("backtrans-text").textContent = i.back_translation;
    const chips = $("number-chips"); chips.innerHTML = "";
    if (fid) {
      fid.numbers_expected.forEach((n) => { const c = document.createElement("span"); const missing = fid.numbers_missing.includes(n); c.className = "chip " + (missing ? "missing" : "kept"); c.textContent = n; chips.appendChild(c); });
      setSeam(fid);
    }
    (i.flagged_terms || []).forEach((t) => { const c = document.createElement("span"); c.className = "chip"; c.title = t.note; c.textContent = "term: " + t.term; chips.appendChild(c); });
  }

  function renderVerdict(v, run) {
    $("verdict-card").hidden = false;
    const div = document.createElement("div"); div.className = "verdict";
    div.innerHTML = `<div><span class="decision ${esc(v.decision)}">${esc(v.decision).toUpperCase()}</span> ${v.rule_id ? `<span class="chip">${esc(v.rule_id)}</span>` : ""} <span class="muted small">critic run ${run}</span></div>
      ${(v.checks || []).map((c) => `<div class="check"><span class="${c.passed ? "ok" : "fail"}">${c.passed ? "ok" : "FAIL"}</span><span>${esc(c.name)}: ${esc(c.detail)}</span></div>`).join("")}
      ${(v.revision_notes || []).map((n) => `<div class="note">→ ${esc(n)}</div>`).join("")}`;
    $("verdict-log").appendChild(div);
    const agent = $("agent-critic"); const st = agent.querySelector(".state");
    st.textContent = v.decision === "refuse" ? `refused · ${v.rule_id || ""}` : v.decision === "revise" ? "sent the draft back" : "approved";
    st.className = "state " + (v.decision === "approve" ? "good" : "bad");
    if (v.decision !== "approve") agent.classList.add(v.decision === "refuse" ? "refused" : "rejected");
  }

  function renderDrafts() {
    $("draft-card").hidden = false; const log = $("draft-log"); log.innerHTML = "";
    state.drafts.forEach((d, idx) => {
      const later = state.drafts[idx + 1];
      const laterLines = later ? new Set(later.body_en.split("\n").map((l) => l.trim())) : null;
      const body = d.body_en.split("\n").map((line) => laterLines && line.trim() && !laterLines.has(line.trim()) ? `<span class="removed">${esc(line)}</span>` : esc(line)).join("\n");
      const div = document.createElement("div"); div.className = "draft";
      div.innerHTML = `<div><strong>v${d.revision}</strong> · ${esc(d.kind)} · ${esc(d.title)}${d.assumptions?.length ? ` <span class="chip missing">assumptions: ${d.assumptions.length}</span>` : ""}</div><pre>${body}</pre>`;
      log.appendChild(div);
    });
  }

  function renderCard(result) {
    const c = result.card; if (!c) return;
    const L = labels();
    $("handoff-editor").hidden = !["en", "es"].includes(state.language);
    $("lbl-who").textContent = L.who; $("lbl-next").textContent = L.next; $("lbl-when").textContent = L.when; $("lbl-safe").textContent = L.safe;
    $("card-headline").textContent = c.headline_target;
    presentParagraphs($("card-statement"), c.statement_target);
    presentSourceFacts(result.reading);
    $("card-who").textContent = c.who_target;
    $("card-next").textContent = c.next_step_target;
    $("card-when").textContent = c.when_target;
    $("card-safe").textContent = c.safe_today_target;
    $("card-rule").textContent = c.rule_citation ? `${result.guard.rule_id} · ${c.rule_citation}` : "";
    const card = $("visitor-card"); card.hidden = false; card.className = "card visitor-card " + result.outcome;
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    const draft = approvedDraft(result);
    $("approved-reply").hidden = !draft;
    $("approved-reply-heading").textContent = L.reply;
    $("approved-reply-text").textContent = draft?.body_target || "";
    const sourceForm = draft ? result.source_form : null;
    const formHeading = state.language === "es" ? "Referencia: campos del documento original" : "Reference: fields from the original document";
    const formNote = state.language === "es" ? "Este fragmento se conserva en inglés, sin cambios. Complete el documento original; estas notas no sustituyen su consentimiento ni su firma." : "This English excerpt is unchanged. Complete the original document; these notes do not replace your consent or signature.";
    $("original-form").hidden = !sourceForm;
    $("original-form-heading").textContent = sourceForm ? formHeading : "";
    $("original-form-note").textContent = sourceForm ? formNote : "";
    $("original-form-text").textContent = sourceForm || "";
    $("print-summary").disabled = false; $("print-summary").textContent = draft ? L.print : L.printSummary;
    $("visitor-handoff").hidden = !state.handoff;
    $("visitor-handoff").innerHTML = state.handoff ? handoffMarkup(state.handoff, state.language) : "";
    $("print-area").innerHTML = `<h1>${esc(c.headline_en)}</h1><p>${esc(c.summary_en)}</p><p class="small">${esc(c.statement_en)}</p><dl><dt>Who</dt><dd>${esc(c.who)}</dd><dt>Next</dt><dd>${esc(c.next_step_en)}</dd><dt>When</dt><dd>${esc(c.when)}</dd><dt>Safe today</dt><dd>${esc(c.safe_today_en)}</dd></dl><hr><section lang="${esc(state.language)}"><h1>${esc(c.headline_target)}</h1><p>${esc(c.summary_target)}</p><p class="small">${esc(c.statement_target)}</p><dl><dt>${esc(L.who)}</dt><dd>${esc(c.who_target)}</dd><dt>${esc(L.next)}</dt><dd>${esc(c.next_step_target)}</dd><dt>${esc(L.when)}</dt><dd>${esc(c.when_target)}</dd><dt>${esc(L.safe)}</dt><dd>${esc(c.safe_today_target)}</dd></dl></section>${draft ? `<section class="printed-reply"><h2>Draft to review with staff</h2><pre>${esc(draft.body_en)}</pre><h2>${esc(L.reply)}</h2><pre lang="${esc(state.language)}">${esc(draft.body_target)}</pre></section>` : ""}${sourceForm ? `<section class="printed-source"><h2>${esc(formHeading)}</h2><p>${esc(formNote)}</p><pre lang="en">${esc(sourceForm)}</pre></section>` : ""}<p class="small">${esc(c.rule_citation || "")}</p><p class="small">Front Desk · ${result.provider === "fake" ? "Deterministic demonstration" : "Review this page with staff"}</p>`;
    if (state.handoff) {
      $("print-area").insertAdjacentHTML("beforeend", `<section class="printed-handoff" lang="en">${handoffMarkup(state.handoff, "en")}<hr><section lang="${esc(state.language)}">${handoffMarkup(state.handoff, state.language)}</section></section>`);
    }
  }

  function handoffMarkup(plan, language) {
    const d = FrontDeskHandoff.describe(plan, language), w = d.words;
    return `<h3>${esc(w.title)}</h3><p class="small">${esc(w.intro)}</p><dl class="handoff-facts"><dt>${esc(w.owner)}</dt><dd>${esc(plan.owner)}</dd><dt>${esc(w.place)}</dt><dd>${esc(plan.place)}</dd><dt>${esc(w.action)}</dt><dd>${esc(d.action)}</dd><dt>${esc(w.when)}</dt><dd>${esc(d.when)}</dd><dt>${esc(w.by)}</dt><dd>${esc(plan.recordedBy)}</dd><dt>${esc(w.at)}</dt><dd>${esc(d.recordedAt)}</dd></dl><p class="small">${esc(w.note)}</p>`;
  }

  function refreshHandoffTiming() {
    const scheduled = $("handoff-mode").value === "scheduled";
    $("handoff-time-field").hidden = !scheduled; $("handoff-time").required = scheduled;
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const date = new Date($("handoff-time").value);
    $("handoff-time-help").textContent = scheduled && Number.isFinite(date.getTime()) ? FrontDeskHandoff.timeText(date.toISOString(), "en", zone) : `Times use this browser's timezone: ${zone}.`;
  }

  function approvedDraft(result) {
    if (result?.outcome !== "proceed" || result.verdicts?.at(-1)?.decision !== "approve") return null;
    const draft = result.drafts?.at(-1);
    return draft && draft.kind !== "none" ? draft : null;
  }

  function showError(detail, code) {
    $("session-error").textContent = code === "runtime_configuration" ? `The reading did not finish. ${detail}` : `The reading did not finish. ${detail} Choose “Choose another letter” to try again, or “New visitor” to clear this visit.`;
    $("session-error").hidden = false;
    document.querySelectorAll(".agent.running").forEach((li) => { li.classList.remove("running"); li.querySelector(".state").textContent = "stopped"; });
  }

  async function startSession(body, title) {
    if (!state.consented) return;
    resetSession(); state.docTitle = title; $("doc-chip").textContent = title; show("session");
    const generation = state.generation;
    const controller = new AbortController(); state.request = controller;
    try {
      const res = await fetch("/api/run", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ...body, consent_token: state.meta.consent_token }), signal: controller.signal });
      if (!res.ok || !res.body) throw new Error(`Server returned ${res.status}.`);
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
      if (!state.result) throw new Error("The connection ended before a result arrived.");
    } catch (error) {
      if (generation === state.generation && error.name !== "AbortError") {
        if (error.code === "model_call_limit" || state.meta.backend !== "local") {
          resetSession();
          $("reading-card").innerHTML = '<h2>Reading stopped</h2><p>Ask staff for help before trying again.</p>';
          $("visitor-text").textContent = state.language === "es" ? "La lectura no terminó. Pida ayuda al personal." : "The reading did not finish. Ask staff for help.";
        }
        if (error.code === "runtime_configuration") state.consented = false;
        showError(error.message, error.code);
      }
    } finally {
      if (generation === state.generation) state.request = null;
    }
  }

  function handle(ev) {
    if (ev.event === "node_start") {
      const li = $("agent-" + ev.node_id); li.classList.remove("done"); li.classList.add("running");
      li.querySelector(".state").textContent = ev.run > 1 ? `running · run #${ev.run}` : "running";
    } else if (ev.event === "node_done") {
      const li = $("agent-" + ev.node_id); li.classList.remove("running"); li.classList.add("done");
      const st = li.querySelector(".state"); if (!st.textContent.startsWith("refused") && !st.textContent.startsWith("sent")) st.textContent = `${ev.status} · ${ev.execution_ms} ms${ev.run > 1 ? ` · run #${ev.run}` : ""}`;
      if (ev.node_id === "reader" && ev.output) renderReading(ev.output, ev.reading_policy);
      if (ev.node_id === "interpreter" && ev.output) renderInterpretation(ev.output, ev.fidelity);
      if (ev.node_id === "drafter" && ev.output) { state.drafts.push(ev.output); renderDrafts(); }
      if (ev.node_id === "critic" && ev.output) renderVerdict(ev.output, ev.run);
    } else if (ev.event === "result") {
      state.result = ev.result;
      if (ev.result.reading_policy && ev.result.reading) renderReading(ev.result.reading, ev.result.reading_policy);
      state.meta.roster.forEach((a) => { if (!ev.result.execution_order.includes(a.id)) { const li = $("agent-" + a.id); li.classList.add("skipped"); li.querySelector(".state").textContent = "skipped: the graph branched around it"; } });
      if (ev.result.fidelity && ev.result.fidelity.band) setSeam(ev.result.fidelity);
      renderCard(ev.result);
    } else if (ev.event === "error") {
      $("reading-card").innerHTML = `<h2>Error</h2><p>${esc(ev.detail)}</p>`;
    }
  }

  function readAloud() {
    if (!("speechSynthesis" in window)) { $("speech-status").textContent = "Audio is unavailable in this browser. Ask staff for reading assistance."; return; }
    const c = state.result?.card;
    const draft = approvedDraft(state.result);
    let text = c ? [c.headline_target, c.statement_target, c.who_target, c.next_step_target, c.when_target, c.safe_today_target, c.summary_target, draft?.body_target].filter(Boolean).join(". ") : state.interpretationText || $("visitor-text").textContent;
    if (state.handoff) {
      const d = FrontDeskHandoff.describe(state.handoff, state.language);
      text += ". " + [d.words.title, d.words.intro, state.handoff.owner, state.handoff.place, d.action, d.when, d.words.note].join(". ");
    }
    const voice = speechSynthesis.getVoices().find((v) => v.lang.toLowerCase().startsWith(state.language));
    if (!voice) { $("speech-status").textContent = "No voice is installed for this language. Ask staff for reading assistance."; return; }
    const u = new SpeechSynthesisUtterance(text);
    u.voice = voice; u.lang = voice.lang; u.rate = 0.92;
    $("speech-status").textContent = "";
    u.onerror = () => { $("speech-status").textContent = "Audio stopped. Ask staff for reading assistance."; };
    speechSynthesis.cancel(); speechSynthesis.speak(u);
  }

  $("handoff-form").oninput = (event) => {
    $("handoff-error").textContent = "";
    if (event.target.id !== "handoff-attest") $("handoff-attest").checked = false;
    $("handoff-edit-status").textContent = state.handoff ? "Changes are not recorded yet. The previous confirmation remains on the takeaway until you confirm again or withdraw it." : "";
    refreshHandoffTiming();
  };
  $("handoff-form").onsubmit = (event) => {
    event.preventDefault(); if (!state.result?.card || !["en", "es"].includes(state.language)) return;
    try {
      const plan = FrontDeskHandoff.create({ owner: $("handoff-owner").value, place: $("handoff-place").value, recordedBy: $("handoff-recorded-by").value, action: $("handoff-action").value, mode: $("handoff-mode").value, localTime: $("handoff-time").value, attested: $("handoff-attest").checked });
      state.handoff = plan; $("handoff-error").textContent = ""; $("handoff-edit-status").textContent = "Confirmation recorded for this visit. Print the takeaway before clearing it.";
      $("handoff-withdraw").hidden = false; renderCard(state.result);
      $("visitor-handoff").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) { $("handoff-error").textContent = error.message; }
  };
  $("handoff-withdraw").onclick = () => {
    state.handoff = null; $("handoff-form").reset(); $("handoff-withdraw").hidden = true;
    $("handoff-error").textContent = ""; $("handoff-edit-status").textContent = "Confirmation withdrawn. The takeaway shows the original suggested next step.";
    refreshHandoffTiming(); if (state.result?.card) renderCard(state.result);
  };

  $("consent-confirm").onchange = () => { $("consent-agree").disabled = !$("consent-confirm").checked; };
  $("consent-agree").onclick = async () => {
    if ($("consent-agree").disabled) return;
    state.consented = true;
    try { await loadFixtures(); if (state.consented) show("document"); }
    catch { $("consent-status").textContent = "Could not load the letters. Please try again."; }
  };
  $("consent-decline").onclick = () => {
    state.consented = false; $("consent-agree").disabled = true; $("consent-confirm").checked = false;
    $("consent-status").textContent = state.language === "es" ? "No se enviará ninguna carta. Pida al personal otra forma de ayuda." : "No letter will be sent. Ask staff about another way to get help.";
  };
  $("consent-back").onclick = newVisitor;
  $("choose-document").onclick = () => { resetSession(); show("document"); };
  $("run-paste").onclick = () => { const text = $("paste").value.trim(); if (!text) return; startSession({ document_text: text, title: $("paste-title").value || "Pasted letter", language: state.language }, $("paste-title").value || "Pasted letter"); };
  $("privacy-toggle").onclick = () => { state.privacy = !state.privacy; document.body.classList.toggle("privacy", state.privacy); $("privacy-toggle").textContent = state.privacy ? "Staff view" : "Privacy mode"; };
  $("read-aloud").onclick = readAloud;
  $("print-summary").onclick = () => { if (state.result?.card) window.print(); };
  $("new-session").onclick = newVisitor;

  $("reload-service").onclick = () => window.location.reload();
  loadMeta().then(() => show("language")).catch((error) => {
    $("provider-line").textContent = error.message;
    $("startup-error").textContent = error.message;
    $("service-unavailable").hidden = false;
  });
})();
