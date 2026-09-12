/* Action queue and approvals: renders ledger actions and receipts, and posts a member's approve/decline with a PIN.
   Only adults ever appear as approvers; the server refuses a minor as well. No raw JSON reaches the screen. */
(() => {
  const ACTION_WORDS = {
    "email:send": "Send an email",
    "payment:transfer": "Pay",
    "allowance:transfer": "Allowance",
    "benefits:claim": "Prepare a benefits claim",
    "form:prepare": "Prepare a form",
    "recall:remedy": "Recall remedy",
    "flight:claim": "Flight claim",
  };
  const STATUS_WORDS = { proposed: "proposed", "needs-approval": "waiting for approval", allowed: "allowed", executed: "done", declined: "declined", blocked: "blocked" };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function when(iso) {
    const date = new Date(iso);
    return Number.isFinite(date.getTime()) ? date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : String(iso || "");
  }

  function modeChip(mode) {
    return el("span", `chip mode mode-${mode}`, mode);
  }

  function statusChip(status) {
    return el("span", `chip status-${status}`, STATUS_WORDS[status] || status);
  }

  function outcomeChip(outcome) {
    return el("span", `chip outcome-${outcome}`, outcome === "needs-approval" ? "needs approval" : outcome);
  }

  function describe(action, ctx) {
    const words = ACTION_WORDS[action.action_type] || action.action_type;
    const subject = ctx.name(action.subject_member_id);
    const amount = action.amount ? `${action.amount} ${action.currency}` : "";
    const target = action.recipient ? ` to ${action.recipient}` : "";
    return { title: `${words}${amount ? " " + amount : ""}${target}`, sub: `for ${subject} · on ${ctx.railName(action.rail)}`, amount };
  }

  function receiptRow(receipt, ctx) {
    const row = el("div", "receipt");
    row.dataset.receiptId = receipt.id;
    row.appendChild(modeChip(receipt.mode));
    const body = el("div", "body");
    const what = receipt.action_type
      ? `${ACTION_WORDS[receipt.action_type] || receipt.action_type}${receipt.amount ? " " + receipt.amount + " " + receipt.currency : ""} for ${ctx.name(receipt.subject_member_id)}`
      : `Action ${receipt.action_id}`;
    body.appendChild(el("div", "what", what));
    body.appendChild(el("div", "reason", `${ctx.railName(receipt.rail)} · ${receipt.label_reason}`));
    const ref = [receipt.provider_ref ? `ref ${receipt.provider_ref}` : "no provider reference", receipt.executed_under_grant ? `under ${receipt.executed_under_grant}` : "", when(receipt.at)].filter(Boolean).join(" · ");
    body.appendChild(el("div", "ref", ref));
    row.appendChild(body);
    return row;
  }

  function renderReceipts(container, receipts, ctx) {
    container.replaceChildren();
    if (!receipts.length) { container.appendChild(el("p", "empty", "No receipts yet. A receipt only exists when the executor ran an allowed action.")); return; }
    receipts.forEach((receipt) => container.appendChild(receiptRow(receipt, ctx)));
  }

  async function post(url, body) {
    const response = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
      const detail = data && typeof data.detail === "string" ? data.detail : `The request failed (${response.status}).`;
      throw Object.assign(new Error(detail), { status: response.status });
    }
    return data;
  }

  function approvalForm(action, ctx) {
    const form = el("form", "approval-form");
    form.dataset.actionId = action.id;
    const approvers = (action.approver_ids || []).map((id) => ctx.member(id)).filter((m) => m && m.role === "adult");
    const who = el("div", "who");
    if (!approvers.length) {
      who.appendChild(el("span", "", "No adult approver is on file for this action."));
      form.appendChild(who);
      return form;
    }
    const label = el("label", "", "Approver ");
    const select = el("select");
    select.name = "approver";
    approvers.forEach((m) => { const option = el("option", "", `${m.name} (${m.role})`); option.value = m.id; select.appendChild(option); });
    label.appendChild(select);
    who.appendChild(label);
    const pinLabel = el("label", "", "PIN ");
    const pin = el("input"); pin.type = "password"; pin.inputMode = "numeric"; pin.autocomplete = "off"; pin.name = "pin"; pin.required = true; pin.maxLength = 12;
    pinLabel.appendChild(pin);
    who.appendChild(pinLabel);
    form.appendChild(who);
    const buttons = el("div", "buttons");
    const approve = el("button", "btn primary approve", "Approve"); approve.type = "submit"; approve.dataset.kind = "approve";
    const decline = el("button", "btn ghost decline", "Decline"); decline.type = "button"; decline.dataset.kind = "decline";
    buttons.append(approve, decline);
    form.appendChild(buttons);
    const error = el("p", "error-line"); error.setAttribute("role", "alert");
    form.appendChild(error);

    async function submit(kind) {
      error.textContent = "";
      if (!pin.value) { error.textContent = "Enter the approver's PIN."; return; }
      approve.disabled = decline.disabled = true;
      try {
        const data = await post(`/api/actions/${encodeURIComponent(action.id)}/${kind}`, { approver_member_id: select.value, pin: pin.value });
        pin.value = "";
        form.replaceWith(decided(data, kind, ctx));
        if (ctx.onDecided) ctx.onDecided(action.id, data, kind);
      } catch (err) {
        error.textContent = err.message;
        approve.disabled = decline.disabled = false;
      }
    }
    form.onsubmit = (event) => { event.preventDefault(); submit("approve"); };
    decline.onclick = () => submit("decline");
    return form;
  }

  function decided(data, kind, ctx) {
    const box = el("div", "decided");
    const line = el("div", "explain");
    line.appendChild(outcomeChip(data.decision.outcome));
    line.appendChild(document.createTextNode(" " + data.explanation));
    box.appendChild(line);
    if (data.receipt) box.appendChild(receiptRow({ ...data.receipt, action_type: data.action?.action_type, amount: data.action?.amount, currency: data.action?.currency, subject_member_id: data.action?.subject_member_id }, ctx));
    else box.appendChild(el("p", "why", kind === "decline" ? "Nothing moved. The decline is recorded as a consent in the ledger." : "Nothing moved."));
    return box;
  }

  function actionCard(action, ctx) {
    const card = el("div", "action");
    card.dataset.actionId = action.id;
    card.dataset.status = action.status;
    const d = describe(action, ctx);
    const head = el("div", "head");
    head.appendChild(el("span", "title", d.title));
    head.appendChild(statusChip(action.status));
    card.appendChild(head);
    card.appendChild(el("div", "why", `${d.sub} · asked by ${ctx.name(action.actor_member_id)} · ${when(action.created_at)}`));
    if (action.rationale) card.appendChild(el("div", "why", action.rationale));
    if (action.decision) {
      const explain = el("div", "explain");
      explain.appendChild(outcomeChip(action.decision.outcome));
      explain.appendChild(document.createTextNode(" " + action.decision.explanation));
      card.appendChild(explain);
    }
    if (action.receipt) card.appendChild(receiptRow({ ...action.receipt, action_type: action.action_type, amount: action.amount, currency: action.currency, subject_member_id: action.subject_member_id }, ctx));
    if (action.status === "needs-approval") card.appendChild(approvalForm({ id: action.id, approver_ids: action.decision ? action.decision.approver_ids : [] }, ctx));
    return card;
  }

  function renderQueue(container, actions, ctx) {
    container.replaceChildren();
    const groups = [
      ["Waiting for approval", actions.filter((a) => a.status === "needs-approval")],
      ["Done", actions.filter((a) => a.status === "executed" || a.status === "allowed")],
      ["Declined or blocked", actions.filter((a) => a.status === "declined" || a.status === "blocked")],
      ["Proposed", actions.filter((a) => a.status === "proposed")],
    ];
    let any = false;
    groups.forEach(([title, items]) => {
      if (!items.length) return;
      any = true;
      const card = el("article", "card");
      card.appendChild(el("h2", "", title));
      items.forEach((action) => card.appendChild(actionCard(action, ctx)));
      container.appendChild(card);
    });
    if (!any) {
      const card = el("article", "card");
      card.appendChild(el("h2", "", "Queue"));
      card.appendChild(el("p", "empty", "Nothing queued. Ask for something and the plan will show up here."));
      container.appendChild(card);
    }
  }

  window.HouseholdQueue = { renderQueue, renderReceipts, approvalForm, receiptRow, modeChip, statusChip, outcomeChip, describe, when, el, post, ACTION_WORDS };
})();
