/*
 * Honeywell Smile Connect - weekly heating schedule card.
 *
 * Reproduces the look and interaction of Home Assistant's own Schedule
 * helper editor (a FullCalendar timeGridWeek), for a room's switching
 * times on a Smile Connect gateway.
 *
 * Deliberate constraints, all load-bearing:
 *  - Hand-written ES module. No build step, no npm, no CDN, no lit. The
 *    file is served straight out of the integration's frontend/ directory
 *    (see __init__.py), so what ships is what runs.
 *  - No reliance on lazily-registered HA internals (ha-card, ha-dialog,
 *    ha-time-input, ha-full-calendar). They are code-split and may not be
 *    defined when this module loads on an arbitrary dashboard. Everything
 *    here is plain DOM plus HA's theme CSS custom properties, so the card
 *    still inherits light/dark and custom themes with no extra work.
 *  - Nothing but the customElements.define() happens at import time. This
 *    module is injected into EVERY dashboard via add_extra_js_url, so a
 *    throw at module scope would degrade the whole frontend.
 *
 * Data flow: it reads a room's plan from the schedule sensor's attributes
 * (sensor.py) - reactive, so a change made in the Smile App shows up on
 * its own - and writes through the existing
 * honeywell_smileconnect.set_schedule_room Action, adopting that Action's
 * freshly-re-read response as truth rather than its own optimistic view.
 *
 * Change log:
 *  - 2026-09-21: Initial version (integration 0.4.0).
 */

const CARD_TAG = "smileconnect-schedule-card";
const DOMAIN = "honeywell_smileconnect";
const SET_SCHEDULE_SERVICE = "set_schedule_room";

// Must match const.SCHEDULE_FORMAT_VERSION. If the sensor reports anything
// else, this card refuses to render rather than risk misreading a plan and
// writing the misreading back.
const SCHEDULE_FORMAT = "honeywell_smileconnect_week_v1";

const WEEKDAYS = [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday",
];

const MINUTES_PER_DAY = 1440;
const DEFAULT_STEP = 15;
const ALLOWED_STEPS = [5, 10, 15, 20, 30, 60];
const MOVE_THRESHOLD_PX = 3;
const COMPACT_BLOCK_MINUTES = 45;

/*
 * Whether a slot may end exactly at midnight.
 *
 * The gateway requires from < to and never wraps, but whether it ACCEPTS
 * "24:00" as a `to` value has not been verified live (our own conversion
 * layer would pass it through). Until a round-trip test against the real
 * gateway confirms it, the last selectable end is one step before
 * midnight - a value that cannot possibly be rejected. Flipping this to
 * true is the only change needed afterwards.
 */
const ALLOW_MIDNIGHT_END = false;

const TYPE_H = "H";
const TYPE_L = "L";

const STRINGS = {
  en: {
    days: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    daysLong: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    title: "Heating schedule",
    typeH: "Comfort Hi",
    typeL: "Comfort Lo",
    typeN: "Night",
    start: "Start",
    end: "End",
    type: "Type",
    save: "Save",
    cancel: "Cancel",
    delete: "Delete",
    close: "Close",
    newSlot: "New time slot",
    editSlot: "Edit time slot",
    errNoEntity: "No schedule entity configured.",
    errUnknownEntity: "Entity not found: ",
    errBadFormat: "This schedule has a format this card does not understand. Update the card and the integration to matching versions.",
    errNoClimate: "The room's thermostat entity could not be found, so the schedule cannot be edited.",
    errUnsupportedType: "This schedule contains a slot type this integration cannot write back (Night). Editing is disabled so nothing is lost.",
    errDayFull: "This day already has the maximum number of time slots.",
    errNoRoom: "There is no free space for this time slot.",
    errEndAfterStart: "The end time must be after the start time.",
    errReadOnlySlot: "Night slots are read-only.",
    saving: "Saving…",
  },
  de: {
    days: ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
    daysLong: ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
    title: "Heizplan",
    typeH: "Komfort Hoch",
    typeL: "Komfort Niedrig",
    typeN: "Nacht",
    start: "Beginn",
    end: "Ende",
    type: "Typ",
    save: "Speichern",
    cancel: "Abbrechen",
    delete: "Löschen",
    close: "Schließen",
    newSlot: "Neuer Zeitabschnitt",
    editSlot: "Zeitabschnitt bearbeiten",
    errNoEntity: "Keine Zeitplan-Entität konfiguriert.",
    errUnknownEntity: "Entität nicht gefunden: ",
    errBadFormat: "Dieser Zeitplan hat ein Format, das diese Karte nicht kennt. Karte und Integration auf zueinander passende Versionen aktualisieren.",
    errNoClimate: "Die Thermostat-Entität des Raums wurde nicht gefunden, der Zeitplan kann deshalb nicht bearbeitet werden.",
    errUnsupportedType: "Dieser Zeitplan enthält einen Abschnittstyp, den die Integration nicht zurückschreiben kann (Nacht). Die Bearbeitung ist deaktiviert, damit nichts verloren geht.",
    errDayFull: "Dieser Tag hat bereits die maximale Anzahl an Zeitabschnitten.",
    errNoRoom: "Für diesen Zeitabschnitt ist kein Platz frei.",
    errEndAfterStart: "Das Ende muss nach dem Beginn liegen.",
    errReadOnlySlot: "Nacht-Abschnitte sind schreibgeschützt.",
    saving: "Speichern…",
  },
  es: {
    days: ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
    daysLong: ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"],
    title: "Programación de calefacción",
    typeH: "Confort alto",
    typeL: "Confort bajo",
    typeN: "Noche",
    start: "Inicio",
    end: "Fin",
    type: "Tipo",
    save: "Guardar",
    cancel: "Cancelar",
    delete: "Eliminar",
    close: "Cerrar",
    newSlot: "Nuevo intervalo",
    editSlot: "Editar intervalo",
    errNoEntity: "No hay ninguna entidad de programación configurada.",
    errUnknownEntity: "Entidad no encontrada: ",
    errBadFormat: "Esta programación tiene un formato que esta tarjeta no reconoce. Actualiza la tarjeta y la integración a versiones compatibles.",
    errNoClimate: "No se ha encontrado la entidad de termostato de la habitación, por lo que la programación no se puede editar.",
    errUnsupportedType: "Esta programación contiene un tipo de intervalo que la integración no puede escribir (Noche). La edición está desactivada para no perder nada.",
    errDayFull: "Este día ya tiene el número máximo de intervalos.",
    errNoRoom: "No hay espacio libre para este intervalo.",
    errEndAfterStart: "La hora de fin debe ser posterior a la de inicio.",
    errReadOnlySlot: "Los intervalos de noche son de solo lectura.",
    saving: "Guardando…",
  },
  fr: {
    days: ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"],
    daysLong: ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"],
    title: "Programmation du chauffage",
    typeH: "Confort haut",
    typeL: "Confort bas",
    typeN: "Nuit",
    start: "Début",
    end: "Fin",
    type: "Type",
    save: "Enregistrer",
    cancel: "Annuler",
    delete: "Supprimer",
    close: "Fermer",
    newSlot: "Nouvelle plage horaire",
    editSlot: "Modifier la plage horaire",
    errNoEntity: "Aucune entité de programmation configurée.",
    errUnknownEntity: "Entité introuvable : ",
    errBadFormat: "Cette programmation utilise un format inconnu de cette carte. Mettez la carte et l'intégration à des versions compatibles.",
    errNoClimate: "L'entité thermostat de la pièce est introuvable, la programmation ne peut donc pas être modifiée.",
    errUnsupportedType: "Cette programmation contient un type de plage que l'intégration ne sait pas réécrire (Nuit). La modification est désactivée pour ne rien perdre.",
    errDayFull: "Ce jour comporte déjà le nombre maximal de plages horaires.",
    errNoRoom: "Aucun espace libre pour cette plage horaire.",
    errEndAfterStart: "L'heure de fin doit être postérieure à l'heure de début.",
    errReadOnlySlot: "Les plages de nuit sont en lecture seule.",
    saving: "Enregistrement…",
  },
};

/* ------------------------------------------------------------------ */
/* Pure helpers - no DOM, no hass                                      */
/* ------------------------------------------------------------------ */

const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));

const pad2 = (value) => String(value).padStart(2, "0");

function minutesToHHMM(minutes) {
  const total = Math.round(minutes);
  return `${pad2(Math.floor(total / 60))}:${pad2(total % 60)}`;
}

function hhmmToMinutes(text) {
  const [hours, mins] = String(text).split(":");
  return Number(hours) * 60 + Number(mins);
}

const snapTo = (minutes, step) => Math.round(minutes / step) * step;

/* Latest end time a slot may have - see ALLOW_MIDNIGHT_END. */
const dayEnd = (step) => (ALLOW_MIDNIGHT_END ? MINUTES_PER_DAY : MINUTES_PER_DAY - step);

/*
 * Free gaps in [0, dayEnd] not covered by `slots`, as [start, end] pairs.
 * Used for both collision clamping (move/resize) and the "is there room"
 * check before creating a slot.
 */
function freeIntervals(slots, step) {
  const busy = [...slots].sort((a, b) => a.start - b.start);
  const gaps = [];
  let cursor = 0;
  for (const slot of busy) {
    if (slot.start > cursor) gaps.push([cursor, slot.start]);
    cursor = Math.max(cursor, slot.end);
  }
  if (cursor < dayEnd(step)) gaps.push([cursor, dayEnd(step)]);
  return gaps;
}

/*
 * Place a block of `duration` as close to `desiredStart` as the free
 * space allows. Returns null when no gap is big enough - the drag is then
 * shown as invalid rather than snapped somewhere surprising.
 */
function clampIntoFreeSpace(slots, desiredStart, duration, step) {
  const gaps = freeIntervals(slots, step).filter(([a, b]) => b - a >= duration);
  if (!gaps.length) return null;
  let best = null;
  let bestDistance = Infinity;
  for (const [a, b] of gaps) {
    const candidate = clamp(desiredStart, a, b - duration);
    const distance = Math.abs(candidate - desiredStart);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = candidate;
    }
  }
  return best;
}

const overlapsAny = (slots, start, end) =>
  slots.some((slot) => start < slot.end && end > slot.start);

const cloneWeek = (week) => {
  const copy = {};
  for (const day of WEEKDAYS) copy[day] = (week[day] || []).map((slot) => ({ ...slot }));
  return copy;
};

const weeksEqual = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/* Minutes model -> the {"from","to","type"} wire shape the Action expects. */
function weekToServiceSchedule(week) {
  const out = {};
  for (const day of WEEKDAYS) {
    out[day] = [...(week[day] || [])]
      .sort((a, b) => a.start - b.start)
      .map((slot) => ({
        from: minutesToHHMM(slot.start),
        to: minutesToHHMM(slot.end),
        type: slot.type,
      }));
  }
  return out;
}

function serviceScheduleToWeek(schedule) {
  const week = {};
  for (const day of WEEKDAYS) {
    week[day] = (schedule[day] || [])
      .map((slot) => ({
        start: hhmmToMinutes(slot.from),
        end: hhmmToMinutes(slot.to),
        type: slot.type,
      }))
      .sort((a, b) => a.start - b.start);
  }
  return week;
}

/* ------------------------------------------------------------------ */

class SmileConnectScheduleCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._stateObj = null;
    this._model = null;
    this._serverModel = null;
    this._meta = null;
    this._drag = null;
    this._writeInFlight = false;
    this._staleIncoming = false;
    this._error = null;
    this._built = false;
    this._nowTimer = null;
  }

  /* ---------------- Lovelace card API ---------------- */

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error(`${CARD_TAG}: 'entity' is required`);
    }
    const step = Number(config.step_minutes ?? DEFAULT_STEP);
    if (!ALLOWED_STEPS.includes(step)) {
      throw new Error(`${CARD_TAG}: step_minutes must be one of ${ALLOWED_STEPS.join(", ")}`);
    }
    this._config = {
      title: null,
      night_gaps: false,
      // 24 x 26px = 624px, so a whole day fits on screen without
      // scrolling on a normal desktop viewport - a heating plan is mostly
      // read as a whole day. Raise it for a more detailed grid.
      hour_height: 26,
      colors: {},
      ...config,
      step_minutes: step,
    };
    this._built = false;
    if (this.shadowRoot) this.shadowRoot.innerHTML = "";
    if (this._hass) this._syncFromHass(true);
  }

  set hass(hass) {
    this._hass = hass;
    this._syncFromHass(false);
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return 12;
  }

  /* Ignored by HA versions without the sections layout; harmless there. */
  getGridOptions() {
    return { columns: "full", rows: 12, min_rows: 8, min_columns: 6 };
  }

  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(
      (id) =>
        id.startsWith("sensor.") &&
        hass.states[id].attributes.schedule_format === SCHEDULE_FORMAT
    );
    return { type: `custom:${CARD_TAG}`, entity: entity || "" };
  }

  connectedCallback() {
    // Keep the "now" line honest without re-rendering the whole grid.
    this._nowTimer = window.setInterval(() => this._positionNowLine(), 60000);
  }

  disconnectedCallback() {
    if (this._nowTimer) window.clearInterval(this._nowTimer);
    this._nowTimer = null;
  }

  /* ---------------- state adoption ---------------- */

  get _t() {
    const language =
      (this._hass && (this._hass.locale?.language || this._hass.language)) || "en";
    return STRINGS[language] || STRINGS[language.split("-")[0]] || STRINGS.en;
  }

  /*
   * Resolve the configured entity. A climate entity is accepted as a
   * convenience (that is what a user naturally looks for), by finding the
   * schedule sensor that points back at it.
   */
  _resolveEntityId() {
    const configured = this._config.entity;
    const states = this._hass.states;
    if (states[configured]?.attributes?.schedule_format === SCHEDULE_FORMAT) {
      return configured;
    }
    if (configured.startsWith("climate.")) {
      return Object.keys(states).find(
        (id) =>
          states[id].attributes.schedule_format === SCHEDULE_FORMAT &&
          states[id].attributes.climate_entity_id === configured
      );
    }
    return states[configured] ? configured : undefined;
  }

  _syncFromHass(force) {
    if (!this._hass || !this._config) return;
    const entityId = this._resolveEntityId();
    const stateObj = entityId ? this._hass.states[entityId] : undefined;
    if (!force && stateObj === this._stateObj) return;
    this._stateObj = stateObj;

    // Never yank the grid out from under an in-progress gesture or write;
    // re-adopt once things settle instead.
    if (this._drag || this._writeInFlight) {
      this._staleIncoming = true;
      return;
    }
    this._adoptFromState();
  }

  _adoptFromState() {
    this._build();
    const t = this._t;
    const stateObj = this._stateObj;

    if (!this._config.entity) return this._renderFatal(t.errNoEntity);
    if (!stateObj) return this._renderFatal(t.errUnknownEntity + this._config.entity);

    const attrs = stateObj.attributes || {};
    if (attrs.schedule_format !== SCHEDULE_FORMAT) return this._renderFatal(t.errBadFormat);

    this._meta = {
      slotsPerDay: Number(attrs.slots_per_day) || 3,
      editable: attrs.editable !== false,
      unsupportedTypes: attrs.unsupported_types || [],
      validTypes: attrs.valid_types || [TYPE_H, TYPE_L],
      climateEntityId: attrs.climate_entity_id || null,
      roomName: attrs.room_name || "",
      temperatures: attrs.desired_temperatures || null,
    };
    this._model = serviceScheduleToWeek(attrs.schedule || {});
    this._serverModel = cloneWeek(this._model);

    if (!this._meta.climateEntityId) this._meta.blockedReason = t.errNoClimate;
    else if (this._meta.unsupportedTypes.length) this._meta.blockedReason = t.errUnsupportedType;
    else this._meta.blockedReason = null;

    this._render();
  }

  _adoptFromResponse(schedule) {
    this._model = serviceScheduleToWeek(schedule);
    this._serverModel = cloneWeek(this._model);
  }

  get _editable() {
    return Boolean(this._meta && this._meta.editable && !this._meta.blockedReason);
  }

  /* ---------------- DOM scaffold ---------------- */

  _build() {
    if (this._built) return;
    this._built = true;
    const step = this._config.step_minutes;

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          --sc-color-H: var(--smileconnect-h-color, var(--error-color, #db4437));
          --sc-color-L: var(--smileconnect-l-color, var(--success-color, #43a047));
          --sc-color-N: var(--smileconnect-n-color, var(--info-color, #039be5));
          --sc-hour-height: ${this._config.hour_height}px;
          --sc-gutter: 52px;
          display: block;
        }
        .card {
          background: var(--ha-card-background, var(--card-background-color, #fff));
          border-radius: var(--ha-card-border-radius, 12px);
          border: var(--ha-card-border-width, 1px) solid
                  var(--ha-card-border-color, var(--divider-color, #e0e0e0));
          box-shadow: var(--ha-card-box-shadow, none);
          color: var(--primary-text-color);
          overflow: hidden;
        }
        .header {
          display: flex; align-items: center; flex-wrap: wrap; gap: 8px 16px;
          padding: 12px 16px 8px 16px;
        }
        .title { font-size: 1.15rem; font-weight: 500; flex: 1 1 auto; }
        .legend { display: flex; gap: 12px; flex-wrap: wrap; }
        .legend span {
          display: inline-flex; align-items: center; gap: 6px;
          font-size: .78rem; color: var(--secondary-text-color);
        }
        .swatch { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
        .banner {
          margin: 0 16px 8px 16px; padding: 8px 12px; border-radius: 8px;
          font-size: .85rem; line-height: 1.35;
          background: var(--secondary-background-color, rgba(0,0,0,.06));
          color: var(--secondary-text-color);
        }
        .banner.error {
          background: var(--error-color, #db4437);
          color: var(--text-primary-color, #fff);
          display: flex; align-items: flex-start; gap: 8px;
        }
        .banner.error button {
          margin-left: auto; background: none; border: none; cursor: pointer;
          color: inherit; font-size: 1rem; line-height: 1; padding: 0 2px;
        }
        .banner[hidden] { display: none; }
        .scroll {
          max-height: 70vh; overflow-y: auto; overflow-x: hidden;
          touch-action: pan-y;
        }
        /* Header cells and day columns share ONE grid, so they stay
           aligned no matter how wide the scrollbar is - two separate
           grids drift apart by exactly the scrollbar width. */
        .grid {
          display: grid;
          grid-template-columns: var(--sc-gutter) repeat(7, minmax(0, 1fr));
        }
        .dayhead {
          position: sticky; top: 0; z-index: 3;
          text-align: center; font-size: .78rem; padding: 4px 0 6px 0;
          color: var(--secondary-text-color);
          background: var(--ha-card-background, var(--card-background-color, #fff));
          border-bottom: 1px solid var(--divider-color, #e0e0e0);
        }
        .dayhead.today { color: var(--primary-color); font-weight: 600; }
        .gutter { position: relative; height: calc(24 * var(--sc-hour-height)); }
        .gutter span {
          position: absolute; right: 6px; transform: translateY(-50%);
          font-size: .7rem; color: var(--secondary-text-color);
        }
        .day-col {
          position: relative;
          height: calc(24 * var(--sc-hour-height));
          border-left: 1px solid var(--divider-color, #e0e0e0);
          /* Solid line each hour, faint one on the half hour - same
             visual rhythm as HA's own schedule editor, and two paints
             instead of 48 positioned elements per column. */
          background-image:
            repeating-linear-gradient(
              to bottom,
              var(--divider-color, #e0e0e0) 0, var(--divider-color, #e0e0e0) 1px,
              transparent 1px, transparent var(--sc-hour-height)),
            repeating-linear-gradient(
              to bottom,
              transparent 0,
              transparent calc(var(--sc-hour-height) / 2),
              rgba(127, 127, 127, .22) calc(var(--sc-hour-height) / 2),
              rgba(127, 127, 127, .22) calc(var(--sc-hour-height) / 2 + 1px),
              transparent calc(var(--sc-hour-height) / 2 + 1px),
              transparent var(--sc-hour-height));
        }
        .day-col.weekend { background-color: rgba(127, 127, 127, .06); }
        .gap-band {
          position: absolute; left: 0; right: 0; pointer-events: none;
          background: var(--sc-color-N); opacity: .18;
        }
        .block {
          position: absolute; left: 2px; right: 2px;
          border-radius: 5px; overflow: hidden;
          color: var(--text-primary-color, #fff);
          font-size: .72rem; line-height: 1.2;
          padding: 3px 5px; box-sizing: border-box;
          touch-action: none; cursor: grab; user-select: none;
          box-shadow: 0 1px 2px rgba(0,0,0,.25);
        }
        .card.saving { opacity: .7; pointer-events: none; }
        .block.readonly, .card.locked .block { cursor: default; }
        .block.readonly { opacity: .85; }
        .block[data-type="H"] { background: var(--sc-color-H); }
        .block[data-type="L"] { background: var(--sc-color-L); }
        .block[data-type="N"] { background: var(--sc-color-N); }
        .block.dragging { cursor: grabbing; z-index: 4; box-shadow: 0 3px 8px rgba(0,0,0,.4); }
        .block.invalid { outline: 2px dashed var(--text-primary-color, #fff); opacity: .55; }
        .block.compact { padding: 1px 5px; font-size: .68rem; }
        .block.compact .block-type { display: none; }
        .block-type { opacity: .85; }
        .handle {
          position: absolute; left: 0; right: 0; height: 8px;
          cursor: ns-resize; touch-action: none;
        }
        .handle.top { top: 0; }
        .handle.bottom { bottom: 0; }
        .card.locked .handle { display: none; }
        .now-line {
          position: absolute; left: 0; right: 0; height: 0;
          border-top: 2px solid var(--accent-color, #ff9800);
          pointer-events: none; z-index: 2;
        }
        .now-line[hidden] { display: none; }
        dialog {
          border: none; padding: 0; border-radius: var(--ha-card-border-radius, 12px);
          background: var(--ha-card-background, var(--card-background-color, #fff));
          color: var(--primary-text-color);
          box-shadow: 0 8px 32px rgba(0,0,0,.35);
          min-width: min(320px, 92vw);
        }
        dialog::backdrop { background: rgba(0, 0, 0, .45); }
        .dlg-body { padding: 16px; display: grid; gap: 12px; }
        .dlg-title { font-size: 1.05rem; font-weight: 500; }
        .dlg-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
        .dlg-row label { color: var(--secondary-text-color); font-size: .85rem; }
        .dlg-row input, .dlg-row select {
          font: inherit; padding: 6px 8px; border-radius: 6px;
          border: 1px solid var(--divider-color, #e0e0e0);
          background: var(--secondary-background-color, transparent);
          color: var(--primary-text-color);
        }
        .dlg-error { color: var(--error-color, #db4437); font-size: .82rem; }
        .dlg-error[hidden] { display: none; }
        .dlg-actions { display: flex; gap: 8px; justify-content: flex-end; padding: 0 16px 16px; }
        .dlg-actions button {
          font: inherit; font-weight: 500; cursor: pointer;
          background: none; border: none; border-radius: 6px; padding: 8px 12px;
          color: var(--primary-color);
        }
        .dlg-actions button:hover { background: var(--secondary-background-color, rgba(0,0,0,.06)); }
        .dlg-actions button.danger { color: var(--error-color, #db4437); margin-right: auto; }
        .dlg-actions button[hidden] { display: none; }
      </style>
      <div class="card">
        <div class="header">
          <div class="title"></div>
          <div class="legend"></div>
        </div>
        <div class="banner info" hidden></div>
        <div class="banner error" hidden><span class="msg"></span><button title="OK">&times;</button></div>
        <div class="scroll"><div class="grid"></div></div>
      </div>
      <dialog>
        <div class="dlg-body">
          <div class="dlg-title"></div>
          <div class="dlg-row"><label class="l-start"></label><input type="time" class="in-start" step="${step * 60}"></div>
          <div class="dlg-row"><label class="l-end"></label><input type="time" class="in-end" step="${step * 60}"></div>
          <div class="dlg-row"><label class="l-type"></label><select class="in-type"></select></div>
          <div class="dlg-error" hidden></div>
        </div>
        <div class="dlg-actions">
          <button class="danger btn-delete"></button>
          <button class="btn-cancel"></button>
          <button class="btn-save"></button>
        </div>
      </dialog>
    `;

    for (const [type, value] of Object.entries(this._config.colors || {})) {
      this.style.setProperty(`--smileconnect-${String(type).toLowerCase()}-color`, value);
    }

    const root = this.shadowRoot;
    this._el = {
      card: root.querySelector(".card"),
      title: root.querySelector(".title"),
      legend: root.querySelector(".legend"),
      info: root.querySelector(".banner.info"),
      error: root.querySelector(".banner.error"),
      errorMsg: root.querySelector(".banner.error .msg"),
      scroll: root.querySelector(".scroll"),
      grid: root.querySelector(".grid"),
      dialog: root.querySelector("dialog"),
    };
    this._el.error.querySelector("button").addEventListener("click", () => this._clearError());
    this._el.grid.addEventListener("pointerdown", (ev) => this._onPointerDown(ev));
    this._el.grid.addEventListener("pointermove", (ev) => this._onPointerMove(ev));
    this._el.grid.addEventListener("pointerup", (ev) => this._onPointerUp(ev));
    this._el.grid.addEventListener("pointercancel", () => this._cancelDrag());
  }

  _renderFatal(message) {
    this._el.title.textContent = this._config.title || this._t.title;
    this._el.legend.innerHTML = "";
    this._el.grid.innerHTML = "";
    this._el.info.hidden = false;
    this._el.info.textContent = message;
  }

  /* ---------------- rendering ---------------- */

  _render() {
    const t = this._t;
    const step = this._config.step_minutes;
    const editable = this._editable;
    this._el.card.classList.toggle("locked", !editable);
    this._el.card.classList.toggle("saving", this._writeInFlight);
    this._el.title.textContent =
      this._config.title || (this._meta.roomName ? `${t.title} – ${this._meta.roomName}` : t.title);

    this._renderLegend();

    this._el.info.hidden = !this._meta.blockedReason;
    if (this._meta.blockedReason) this._el.info.textContent = this._meta.blockedReason;

    const todayIndex = (new Date().getDay() + 6) % 7; // JS Sunday=0 -> Monday=0
    this._el.grid.innerHTML =
      '<div class="dayhead"></div>' +
      WEEKDAYS.map(
        (_, i) => `<div class="dayhead ${i === todayIndex ? "today" : ""}">${t.days[i]}</div>`
      ).join("");

    const gutter = document.createElement("div");
    gutter.className = "gutter";
    for (let hour = 1; hour < 24; hour++) {
      const label = document.createElement("span");
      label.style.top = `${(hour / 24) * 100}%`;
      label.textContent = `${pad2(hour)}:00`;
      gutter.appendChild(label);
    }

    this._el.grid.appendChild(gutter);
    this._columns = [];

    WEEKDAYS.forEach((day, dayIndex) => {
      const column = document.createElement("div");
      column.className = "day-col" + (dayIndex >= 5 ? " weekend" : "");
      column.dataset.day = day;

      if (this._config.night_gaps) {
        for (const [from, to] of freeIntervals(this._model[day], step)) {
          if (to - from < step) continue;
          const band = document.createElement("div");
          band.className = "gap-band";
          band.style.top = `${(from / MINUTES_PER_DAY) * 100}%`;
          band.style.height = `${((to - from) / MINUTES_PER_DAY) * 100}%`;
          column.appendChild(band);
        }
      }

      this._model[day].forEach((slot, index) => {
        column.appendChild(this._buildBlock(day, index, slot, editable));
      });

      if (dayIndex === todayIndex) {
        const nowLine = document.createElement("div");
        nowLine.className = "now-line";
        column.appendChild(nowLine);
        this._nowLine = nowLine;
      }

      this._el.grid.appendChild(column);
      this._columns.push(column);
    });

    this._positionNowLine();
  }

  _renderLegend() {
    const t = this._t;
    const temps = this._meta.temperatures || {};
    const entries = [
      [TYPE_H, t.typeH, temps.comfort_hi],
      [TYPE_L, t.typeL, temps.comfort_lo],
      ["N", t.typeN, temps.night],
    ];
    this._el.legend.innerHTML = entries
      .map(
        ([type, label, temp]) =>
          `<span><i class="swatch" style="background: var(--sc-color-${type})"></i>` +
          `${label}${temp != null ? ` ${temp} °C` : ""}</span>`
      )
      .join("");
  }

  _buildBlock(day, index, slot, editable) {
    const t = this._t;
    const block = document.createElement("div");
    block.className = "block";
    // A type we cannot write back stays visible but untouchable - the
    // integration reports this via unsupported_types, we never guess.
    const readOnly = !editable || !this._meta.validTypes.includes(slot.type);
    if (readOnly) block.classList.add("readonly");
    if (slot.end - slot.start < COMPACT_BLOCK_MINUTES) block.classList.add("compact");
    block.dataset.type = slot.type;
    block.dataset.day = day;
    block.dataset.index = String(index);
    block.style.top = `${(slot.start / MINUTES_PER_DAY) * 100}%`;
    block.style.height = `${((slot.end - slot.start) / MINUTES_PER_DAY) * 100}%`;
    const typeLabel = { H: t.typeH, L: t.typeL, N: t.typeN }[slot.type] || slot.type;
    block.innerHTML =
      `<div class="block-time">${minutesToHHMM(slot.start)}–${minutesToHHMM(slot.end)}</div>` +
      `<div class="block-type">${typeLabel}</div>`;
    block.tabIndex = 0;
    block.setAttribute("role", "button");
    block.setAttribute(
      "aria-label",
      `${t.daysLong[WEEKDAYS.indexOf(day)]} ${minutesToHHMM(slot.start)} – ${minutesToHHMM(slot.end)}, ${typeLabel}`
    );
    block.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        this._openDialog(day, index);
      }
    });
    if (!readOnly) {
      const top = document.createElement("div");
      top.className = "handle top";
      const bottom = document.createElement("div");
      bottom.className = "handle bottom";
      block.append(top, bottom);
    }
    return block;
  }

  _positionNowLine() {
    if (!this._nowLine) return;
    const now = new Date();
    this._nowLine.style.top = `${((now.getHours() * 60 + now.getMinutes()) / MINUTES_PER_DAY) * 100}%`;
  }

  /* ---------------- pointer interaction ---------------- */

  _columnRects() {
    return this._columns.map((column) => ({ column, rect: column.getBoundingClientRect() }));
  }

  _dayFromX(clientX) {
    const rects = this._rects || this._columnRects();
    let index = rects.findIndex(({ rect }) => clientX >= rect.left && clientX <= rect.right);
    if (index === -1) index = clientX < rects[0].rect.left ? 0 : rects.length - 1;
    return index;
  }

  _minutesFromY(clientY) {
    const { rect } = (this._rects || this._columnRects())[0];
    return clamp(((clientY - rect.top) / rect.height) * MINUTES_PER_DAY, 0, MINUTES_PER_DAY);
  }

  _onPointerDown(ev) {
    if (!this._editable || this._writeInFlight || ev.button > 0) return;
    const block = ev.target.closest?.(".block");
    const handle = ev.target.closest?.(".handle");
    const column = ev.target.closest?.(".day-col");
    if (!column) return;

    this._rects = this._columnRects();
    const step = this._config.step_minutes;
    const pointerMinutes = this._minutesFromY(ev.clientY);

    if (block) {
      if (block.classList.contains("readonly")) return;
      const day = block.dataset.day;
      const index = Number(block.dataset.index);
      const slot = this._model[day][index];
      this._drag = {
        mode: handle ? (handle.classList.contains("top") ? "resize-start" : "resize-end") : "move",
        armed: !handle, // a plain block press only becomes a move after real travel
        day,
        index,
        el: block,
        origin: { ...slot },
        curDay: day,
        curStart: slot.start,
        curEnd: slot.end,
        grabOffset: pointerMinutes - slot.start,
        startX: ev.clientX,
        startY: ev.clientY,
        valid: true,
      };
      block.classList.add("dragging");
    } else {
      // Drag-to-create is mouse/pen only: on touch it would fight the
      // page's own vertical scrolling. Touch gets tap-to-add instead.
      if (ev.pointerType === "touch") {
        const day = column.dataset.day;
        this._rects = null;
        this._openDialog(day, null, snapTo(pointerMinutes, step));
        return;
      }
      const day = column.dataset.day;
      if (this._model[day].length >= this._meta.slotsPerDay) {
        this._showError(this._t.errDayFull);
        return;
      }
      const anchor = clamp(snapTo(pointerMinutes, step), 0, dayEnd(step) - step);
      const el = this._buildBlock(day, this._model[day].length, { start: anchor, end: anchor + step, type: TYPE_H }, true);
      el.classList.add("dragging");
      column.appendChild(el);
      this._drag = {
        mode: "create",
        // A press with no travel is a click, and a click opens the
        // dialog rather than silently dropping a one-step block.
        armed: true,
        day,
        index: null,
        el,
        anchor,
        curDay: day,
        curStart: anchor,
        curEnd: anchor + step,
        type: TYPE_H,
        startX: ev.clientX,
        startY: ev.clientY,
        valid: true,
      };
    }
    this._el.grid.setPointerCapture(ev.pointerId);
  }

  _onPointerMove(ev) {
    const drag = this._drag;
    if (!drag) return;
    if (drag.armed) {
      const travelled =
        Math.abs(ev.clientX - drag.startX) + Math.abs(ev.clientY - drag.startY);
      if (travelled < MOVE_THRESHOLD_PX) return;
      drag.armed = false;
    }
    const step = this._config.step_minutes;
    const pointerMinutes = this._minutesFromY(ev.clientY);

    if (drag.mode === "create") {
      const cursor = clamp(snapTo(pointerMinutes, step), 0, dayEnd(step));
      drag.curStart = Math.min(drag.anchor, cursor);
      drag.curEnd = Math.max(drag.anchor, cursor, drag.curStart + step);
      drag.curEnd = Math.min(drag.curEnd, dayEnd(step));
      drag.curStart = Math.min(drag.curStart, drag.curEnd - step);
      // A create that would overlap is refused, not nudged: clamping it
      // would make it ambiguous which slot the user actually meant.
      drag.valid = !overlapsAny(this._model[drag.day], drag.curStart, drag.curEnd);
    } else if (drag.mode === "move") {
      const targetDay = WEEKDAYS[this._dayFromX(ev.clientX)];
      const duration = drag.origin.end - drag.origin.start;
      const others = this._slotsExcludingDragged(targetDay);
      if (this._model[targetDay].length >= this._meta.slotsPerDay && targetDay !== drag.day) {
        drag.valid = false;
      } else {
        const desired = clamp(
          snapTo(pointerMinutes - drag.grabOffset, step),
          0,
          dayEnd(step) - duration
        );
        const placed = clampIntoFreeSpace(others, desired, duration, step);
        if (placed === null) {
          drag.valid = false;
        } else {
          drag.valid = true;
          drag.curDay = targetDay;
          drag.curStart = placed;
          drag.curEnd = placed + duration;
        }
      }
    } else {
      const others = this._slotsExcludingDragged(drag.day);
      const cursor = clamp(snapTo(pointerMinutes, step), 0, dayEnd(step));
      let start = drag.curStart;
      let end = drag.curEnd;
      if (drag.mode === "resize-start") start = Math.min(cursor, drag.curEnd - step);
      else end = Math.max(cursor, drag.curStart + step);
      // Resizing clamps against the neighbour rather than refusing, which
      // is what HA's own editor does and keeps the drag usable.
      for (const slot of others) {
        if (drag.mode === "resize-start" && slot.end <= drag.curStart) {
          start = Math.max(start, slot.end);
        }
        if (drag.mode === "resize-end" && slot.start >= drag.curEnd) {
          end = Math.min(end, slot.start);
        }
      }
      drag.valid = end - start >= step;
      if (drag.valid) {
        drag.curStart = start;
        drag.curEnd = end;
      }
    }
    this._applyDragVisual();
  }

  _slotsExcludingDragged(day) {
    const slots = this._model[day] || [];
    if (this._drag.index === null || day !== this._drag.day) return slots;
    return slots.filter((_, index) => index !== this._drag.index);
  }

  _applyDragVisual() {
    const drag = this._drag;
    const targetColumn = this._columns[WEEKDAYS.indexOf(drag.curDay)];
    if (drag.el.parentElement !== targetColumn) targetColumn.appendChild(drag.el);
    drag.el.style.top = `${(drag.curStart / MINUTES_PER_DAY) * 100}%`;
    drag.el.style.height = `${((drag.curEnd - drag.curStart) / MINUTES_PER_DAY) * 100}%`;
    drag.el.classList.toggle("invalid", !drag.valid);
    drag.el.classList.toggle("compact", drag.curEnd - drag.curStart < COMPACT_BLOCK_MINUTES);
    const timeEl = drag.el.querySelector(".block-time");
    if (timeEl) {
      timeEl.textContent = `${minutesToHHMM(drag.curStart)}–${minutesToHHMM(drag.curEnd)}`;
    }
  }

  _onPointerUp(ev) {
    const drag = this._drag;
    if (!drag) return;
    try {
      this._el.grid.releasePointerCapture(ev.pointerId);
    } catch (err) {
      /* capture may already be gone - not worth reporting */
    }
    this._drag = null;
    this._rects = null;

    // A press that never moved is a click: open the editor instead.
    if (drag.armed) {
      if (drag.mode === "create") {
        drag.el.remove();
        this._openDialog(drag.day, null, drag.anchor);
      } else {
        drag.el.classList.remove("dragging");
        this._openDialog(drag.day, drag.index);
      }
      return;
    }
    if (drag.mode === "create" && !drag.valid) {
      drag.el.remove();
      this._showError(this._t.errNoRoom);
      return;
    }
    if (!drag.valid) {
      this._render();
      return;
    }

    const next = cloneWeek(this._model);
    if (drag.mode === "create") {
      next[drag.day].push({ start: drag.curStart, end: drag.curEnd, type: drag.type });
    } else {
      const [slot] = next[drag.day].splice(drag.index, 1);
      next[drag.curDay].push({ ...slot, start: drag.curStart, end: drag.curEnd });
    }
    for (const day of WEEKDAYS) next[day].sort((a, b) => a.start - b.start);
    this._commit(next);
  }

  _cancelDrag() {
    if (!this._drag) return;
    this._drag = null;
    this._rects = null;
    this._render();
  }

  /* ---------------- dialog ---------------- */

  _openDialog(day, index, presetStart) {
    const t = this._t;
    const step = this._config.step_minutes;
    const dialog = this._el.dialog;
    const root = this.shadowRoot;
    const existing =
      index === null || index === undefined ? null : this._model[day][index] ?? null;
    const readOnly = !this._editable || (existing && !this._meta.validTypes.includes(existing.type));

    if (existing === null && this._model[day].length >= this._meta.slotsPerDay) {
      this._showError(t.errDayFull);
      return;
    }

    const start = existing ? existing.start : clamp(presetStart ?? 0, 0, dayEnd(step) - step);
    const end = existing
      ? existing.end
      : Math.min(start + Math.max(60, step), dayEnd(step));

    root.querySelector(".dlg-title").textContent =
      `${t.daysLong[WEEKDAYS.indexOf(day)]} – ${existing ? t.editSlot : t.newSlot}`;
    root.querySelector(".l-start").textContent = t.start;
    root.querySelector(".l-end").textContent = t.end;
    root.querySelector(".l-type").textContent = t.type;

    const startInput = root.querySelector(".in-start");
    const endInput = root.querySelector(".in-end");
    const typeSelect = root.querySelector(".in-type");
    const errorEl = root.querySelector(".dlg-error");
    const btnDelete = root.querySelector(".btn-delete");
    const btnCancel = root.querySelector(".btn-cancel");
    const btnSave = root.querySelector(".btn-save");

    startInput.value = minutesToHHMM(start);
    endInput.value = minutesToHHMM(end);
    const typeLabels = { H: t.typeH, L: t.typeL, N: t.typeN };
    const options = existing && !this._meta.validTypes.includes(existing.type)
      ? [existing.type]
      : this._meta.validTypes;
    typeSelect.innerHTML = options
      .map((type) => `<option value="${type}">${typeLabels[type] || type}</option>`)
      .join("");
    typeSelect.value = existing ? existing.type : TYPE_H;

    errorEl.hidden = true;
    for (const input of [startInput, endInput, typeSelect]) input.disabled = readOnly;
    btnDelete.hidden = readOnly || !existing;
    btnSave.hidden = readOnly;
    btnDelete.textContent = t.delete;
    btnCancel.textContent = readOnly ? t.close : t.cancel;
    btnSave.textContent = t.save;
    if (readOnly && existing) {
      errorEl.hidden = false;
      errorEl.textContent = t.errReadOnlySlot;
    }

    const close = () => {
      btnCancel.removeEventListener("click", close);
      btnSave.removeEventListener("click", onSave);
      btnDelete.removeEventListener("click", onDelete);
      dialog.close();
    };

    const onSave = () => {
      const newStart = clamp(snapTo(hhmmToMinutes(startInput.value), step), 0, dayEnd(step) - step);
      const newEnd = clamp(snapTo(hhmmToMinutes(endInput.value), step), step, dayEnd(step));
      if (newEnd <= newStart) {
        errorEl.hidden = false;
        errorEl.textContent = t.errEndAfterStart;
        return;
      }
      const others = (this._model[day] || []).filter((_, i) => i !== index);
      if (overlapsAny(others, newStart, newEnd)) {
        errorEl.hidden = false;
        errorEl.textContent = t.errNoRoom;
        return;
      }
      const next = cloneWeek(this._model);
      const slot = { start: newStart, end: newEnd, type: typeSelect.value };
      if (existing) next[day][index] = slot;
      else next[day].push(slot);
      next[day].sort((a, b) => a.start - b.start);
      close();
      this._commit(next);
    };

    const onDelete = () => {
      const next = cloneWeek(this._model);
      next[day].splice(index, 1);
      close();
      this._commit(next);
    };

    btnCancel.addEventListener("click", close);
    btnSave.addEventListener("click", onSave);
    btnDelete.addEventListener("click", onDelete);
    dialog.showModal();
  }

  /* ---------------- write path ---------------- */

  async _commit(next) {
    if (weeksEqual(next, this._model)) return;
    const previous = cloneWeek(this._serverModel);
    this._model = next;
    this._writeInFlight = true;
    this._render();

    try {
      const response = await this._callSetSchedule(weekToServiceSchedule(next));
      if (response) this._adoptFromResponse(response);
      this._clearError();
    } catch (err) {
      // Roll back to the last state the gateway actually confirmed, not to
      // whatever we optimistically drew.
      this._model = previous;
      this._showError(this._errorText(err));
      console.error(`${CARD_TAG}: writing the schedule failed`, err);
    } finally {
      this._writeInFlight = false;
      this._render();
      if (this._staleIncoming) {
        this._staleIncoming = false;
        this._adoptFromState();
      }
    }
  }

  /*
   * Deliberately the raw websocket call rather than hass.callService():
   * return_response support there varies by frontend version, and a
   * fallback retry would risk writing the whole week twice. This shape is
   * what the frontend's own callService is built on.
   */
  async _callSetSchedule(schedule) {
    const result = await this._hass.callWS({
      type: "call_service",
      domain: DOMAIN,
      service: SET_SCHEDULE_SERVICE,
      service_data: { schedule },
      target: { entity_id: this._meta.climateEntityId },
      return_response: true,
    });
    const response = result?.response;
    if (!response) return null;
    // An entity service targeting exactly one entity returns the bare
    // response, but tolerate a per-entity-keyed shape too rather than
    // silently rendering nothing.
    if (WEEKDAYS.some((day) => day in response)) return response;
    const keyed = response[this._meta.climateEntityId];
    return keyed && WEEKDAYS.some((day) => day in keyed) ? keyed : null;
  }

  _errorText(err) {
    if (!err) return "Unknown error";
    return err.message || err.error || String(err);
  }

  _showError(message) {
    this._error = message;
    this._el.error.hidden = false;
    this._el.errorMsg.textContent = message;
    this.dispatchEvent(
      new CustomEvent("hass-notification", {
        detail: { message },
        bubbles: true,
        composed: true,
      })
    );
  }

  _clearError() {
    this._error = null;
    this._el.error.hidden = true;
  }
}

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, SmileConnectScheduleCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === CARD_TAG)) {
  window.customCards.push({
    type: CARD_TAG,
    name: "Honeywell Smile Connect Schedule",
    description: "Weekly heating schedule editor for a Smile Connect room",
    preview: false,
    documentationURL: "https://github.com/djiwondee/honeywell-smileconnect-ha",
  });
}
