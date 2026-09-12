/* Staff-entered session data only: no model, storage, booking or network. */
(() => {
  const COPY = {
    en: { title: "Staff confirmation", intro: "Staff entered this plan after the automated suggestion. It replaces the suggested contact and availability above.", owner: "Responsible person", place: "Meet at", action: "Agreed next step", when: "When", by: "Recorded by", at: "Recorded at", now: "At the desk, confirmed at", scheduled: "Agreed time", review: "Review this letter together.", contact: "Help you contact the appropriate service.", language: "Help explain the letter in your language.", note: "This records a staff agreement. It does not confirm an external booking or completed referral." },
    es: { title: "Confirmación del personal", intro: "El personal añadió este plan después de la sugerencia automática. Sustituye el contacto y la disponibilidad sugeridos arriba.", owner: "Persona responsable", place: "Punto de encuentro", action: "Siguiente paso acordado", when: "Cuándo", by: "Registrado por", at: "Registrado el", now: "En este lugar, confirmado el", scheduled: "Horario acordado", review: "Revisar juntos esta carta.", contact: "Ayudarle a contactar con el servicio adecuado.", language: "Ayudar a explicar la carta en su idioma.", note: "Esto registra un acuerdo con el personal. No confirma una reserva externa ni que se haya completado una derivación." },
  };
  function timeText(iso, language, zone) {
    const date = new Date(iso);
    const text = new Intl.DateTimeFormat(language, { dateStyle: "full", timeStyle: "short", timeZone: zone }).format(date);
    const offset = new Intl.DateTimeFormat("en", { timeZone: zone, timeZoneName: "longOffset" }).formatToParts(date).find(p => p.type === "timeZoneName").value;
    return `${text} (${zone}; ${offset})`;
  }
  function create(values, now = new Date()) {
    const owner = values.owner.trim(), place = values.place.trim(), recordedBy = values.recordedBy.trim();
    if (!owner || !place || !recordedBy || owner.length > 80 || place.length > 120 || recordedBy.length > 80) throw new Error("Enter the responsible person, meeting point and your name.");
    if (!["review", "contact", "language"].includes(values.action)) throw new Error("Choose the agreed next step.");
    if (!["now", "scheduled"].includes(values.mode)) throw new Error("Choose the agreed timing.");
    if (!values.attested) throw new Error("Confirm the agreement and visitor explanation before recording it.");
    let agreedAt = now;
    if (values.mode === "scheduled") {
      agreedAt = new Date(values.localTime);
      if (!values.localTime || !Number.isFinite(agreedAt.getTime()) || agreedAt <= now) throw new Error("Choose a valid future date and time, or select At the desk now.");
      const [day, time] = values.localTime.split("T");
      const local = `${agreedAt.getFullYear()}-${String(agreedAt.getMonth()+1).padStart(2,"0")}-${String(agreedAt.getDate()).padStart(2,"0")}T${String(agreedAt.getHours()).padStart(2,"0")}:${String(agreedAt.getMinutes()).padStart(2,"0")}`;
      if (`${day}T${time}` !== local) throw new Error("That local time does not exist because the clock changes. Choose another time.");
    }
    return { owner, place, recordedBy, action: values.action, mode: values.mode, agreedAt: agreedAt.toISOString(), recordedAt: now.toISOString(), timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone };
  }
  function describe(plan, language) {
    const words = COPY[language] || COPY.en;
    const when = `${words[plan.mode === "now" ? "now" : "scheduled"]}: ${timeText(plan.agreedAt, language, plan.timeZone)}`;
    return { words, when, action: words[plan.action], recordedAt: timeText(plan.recordedAt, language, plan.timeZone) };
  }
  window.FrontDeskHandoff = { create, describe, timeText };
})();
