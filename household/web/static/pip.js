/* Pip, the household mascot. Inline SVG, five moods via data-mood on the .pip wrapper. Vanilla, no build, no network.
   Pip's expression mirrors the worst honesty label on screen; its speech is real text in a sibling element, never baked
   into the drawing, so a screen reader hears the message, not "mascot". Reduced-motion is handled in app.css. */
(() => {
  const MOODS = ["idle", "thinking", "done", "prepared", "waiting"];

  // One drawing carries every expression; app.css shows only the parts that match the wrapper's data-mood.
  const SVG = `
<svg class="pip-svg" viewBox="0 0 100 104" aria-hidden="true" focusable="false">
  <g class="pip-body">
    <path class="pip-roof" d="M31 30 L50 9 L69 30 Z" fill="var(--primary)"/>
    <rect class="pip-head" x="18" y="27" width="64" height="63" rx="25" fill="var(--pip-body)" stroke="var(--pip-outline)" stroke-width="2.5"/>
    <ellipse class="pip-cheek" cx="33" cy="63" rx="7" ry="5" fill="var(--m-coral)"/>
    <ellipse class="pip-cheek" cx="67" cy="63" rx="7" ry="5" fill="var(--m-coral)"/>
    <circle class="pip-antenna" cx="50" cy="9" r="4" fill="var(--primary)"/>
  </g>

  <g class="pip-eyes eyes-open">
    <circle cx="39" cy="54" r="4.4" fill="var(--pip-ink)"/>
    <circle cx="61" cy="54" r="4.4" fill="var(--pip-ink)"/>
    <circle cx="40.6" cy="52.4" r="1.4" fill="var(--pip-glint)"/>
    <circle cx="62.6" cy="52.4" r="1.4" fill="var(--pip-glint)"/>
  </g>
  <g class="pip-eyes eyes-happy">
    <path d="M34 55 q5 -6 10 0" stroke="var(--pip-ink)" stroke-width="3" fill="none" stroke-linecap="round"/>
    <path d="M56 55 q5 -6 10 0" stroke="var(--pip-ink)" stroke-width="3" fill="none" stroke-linecap="round"/>
  </g>

  <g class="pip-brows brows-waiting">
    <path d="M33 46 q6 -3 11 -1" stroke="var(--pip-ink)" stroke-width="2.4" fill="none" stroke-linecap="round"/>
    <path d="M67 46 q-6 -3 -11 -1" stroke="var(--pip-ink)" stroke-width="2.4" fill="none" stroke-linecap="round"/>
  </g>

  <path class="pip-mouth mouth-idle" d="M42 68 q8 7 16 0" stroke="var(--pip-ink)" stroke-width="3" fill="none" stroke-linecap="round"/>
  <path class="pip-mouth mouth-prepared" d="M42 69 q8 4 16 0" stroke="var(--pip-ink)" stroke-width="3" fill="none" stroke-linecap="round"/>
  <path class="pip-mouth mouth-done" d="M40 66 q10 13 20 0 q-10 5 -20 0 Z" fill="var(--pip-ink)"/>
  <ellipse class="pip-mouth mouth-thinking" cx="50" cy="70" rx="4" ry="4.5" fill="var(--pip-ink)"/>
  <path class="pip-mouth mouth-waiting" d="M43 70 q3.5 -3 7 0 q3.5 3 7 0" stroke="var(--pip-ink)" stroke-width="2.8" fill="none" stroke-linecap="round"/>

  <g class="pip-think">
    <circle cx="76" cy="34" r="2.2" fill="var(--secondary-ink)"/>
    <circle cx="83" cy="27" r="3" fill="var(--secondary-ink)"/>
    <circle cx="91" cy="19" r="3.8" fill="var(--secondary-ink)"/>
  </g>

  <g class="pip-hold">
    <rect x="60" y="74" width="30" height="22" rx="4" transform="rotate(9 75 85)" fill="var(--surface)" stroke="var(--pip-outline)" stroke-width="2"/>
    <line x1="66" y1="82" x2="86" y2="85" stroke="var(--secondary-ink)" stroke-width="2" stroke-linecap="round" transform="rotate(9 75 85)"/>
    <line x1="65" y1="88" x2="82" y2="90" stroke="var(--secondary-ink)" stroke-width="2" stroke-linecap="round" transform="rotate(9 75 85)"/>
  </g>
</svg>`;

  function render(node) {
    if (!node || node.dataset.pipReady === "1") return;
    if (!node.dataset.mood) node.dataset.mood = "idle";
    node.classList.add("pip");
    node.innerHTML = SVG;
    node.dataset.pipReady = "1";
  }

  function renderAll(root) {
    (root || document).querySelectorAll(".pip").forEach(render);
  }

  function setMood(node, mood) {
    if (!node) return;
    render(node);
    node.dataset.mood = MOODS.includes(mood) ? mood : "idle";
  }

  // Pip's face reflects the *worst* honesty label on screen: gently waiting for a grown-up, then neutral for a
  // simulation, calm-holding for a prepared form, happy for a completed action. Cuteness never overrides the truth.
  function moodFromModes(modes, needsApproval) {
    if (needsApproval) return "waiting";
    const set = new Set(modes || []);
    if (set.has("SIMULATED") || set.has("SIMULATED-replay")) return "idle";
    if (set.has("PREPARE-ONLY")) return "prepared";
    if (set.has("COMPLETE")) return "done";
    return "idle";
  }

  window.HouseholdPip = { render, renderAll, setMood, moodFromModes, MOODS };
})();
