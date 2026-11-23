class EnergyCostTrackerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._requestId = 0;
    this._loading = false;
    this._error = null;
    this._data = null;
    this._period = null;
    this._energyCollection = null;
    this._energyConnection = null;
    this._unsubscribeEnergyCollection = null;
    this._collectionKey = null;
  }

  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error("Eine gültige Kartenkonfiguration ist erforderlich.");
    }
    const width = config.width ?? 100;
    if (typeof width !== "number" || !Number.isFinite(width) || width < 10 || width > 100) {
      throw new Error("Die Kartenbreite muss zwischen 10 und 100 Prozent liegen.");
    }
    for (const color of ["under_budget_color", "over_budget_color"]) {
      if (config[color] !== undefined && !/^#[0-9a-f]{6}$/i.test(config[color])) {
        throw new Error(`${color} muss ein hexadezimaler Farbwert sein.`);
      }
    }
    const collectionKey = config.collection_key || "energy_dashboard";
    if (typeof collectionKey !== "string" || !collectionKey.startsWith("energy_")) {
      throw new Error("collection_key muss mit 'energy_' beginnen.");
    }
    if (collectionKey !== this._collectionKey) {
      this._unsubscribeEnergyCollection?.();
      this._unsubscribeEnergyCollection = null;
      this._energyCollection = null;
      this._energyConnection = null;
      this._period = null;
      this._data = null;
      this._requestId++;
      this._loading = false;
      this._error = null;
      this._collectionKey = collectionKey;
    }
    this._config = config;
    this.style.width = `${width}%`;
    this.style.maxWidth = "100%";
    this.style.setProperty(
      "--energycosttracker-under-budget-color",
      config.under_budget_color || "#03a9f4",
    );
    this.style.setProperty(
      "--energycosttracker-over-budget-color",
      config.over_budget_color || "#db4437",
    );
    this._ensureEnergyCollectionSubscription();
    this._render();
  }

  getConfigElement() {
    return document.createElement("energycosttracker-card-editor");
  }

  getStubConfig() {
    return {
      type: "custom:energycosttracker-card",
      collection_key: "energy_dashboard",
      width: 100,
      under_budget_color: "#03a9f4",
      over_budget_color: "#db4437",
    };
  }

  set hass(hass) {
    this._hass = hass;
    this._ensureEnergyCollectionSubscription();
  }

  connectedCallback() {
    super.connectedCallback();
    this._ensureEnergyCollectionSubscription();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._unsubscribeEnergyCollection?.();
    this._unsubscribeEnergyCollection = null;
    this._energyCollection = null;
    this._energyConnection = null;
    this._requestId++;
    this._period = null;
    this._data = null;
    this._loading = false;
    this._error = null;
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return 5;
  }

  _getEnergyCollection() {
    if (!this._hass?.connection || !this._collectionKey) {
      return null;
    }
    return this._hass.connection[`_${this._collectionKey}`] || null;
  }

  _ensureEnergyCollectionSubscription() {
    if (!this._config) {
      return;
    }
    if (
      this._energyCollection &&
      this._energyConnection !== this._hass?.connection
    ) {
      this._unsubscribeEnergyCollection?.();
      this._unsubscribeEnergyCollection = null;
      this._energyCollection = null;
      this._energyConnection = null;
      this._period = null;
      this._data = null;
      this._requestId++;
      this._loading = false;
      this._error = null;
    }
    const collection = this._getEnergyCollection();
    if (!collection || collection === this._energyCollection) {
      return;
    }
    this._unsubscribeEnergyCollection?.();
    this._energyCollection = collection;
    this._energyConnection = this._hass.connection;
    this._unsubscribeEnergyCollection = collection.subscribe(() => {
      this._handleEnergyCollectionUpdate(collection);
    });
    this._handleEnergyCollectionUpdate(collection);
  }

  _dateInHomeAssistantTimeZone(value) {
    const timeZone = this._hass?.config?.time_zone;
    if (!timeZone) {
      throw new Error("Die Home-Assistant-Zeitzone ist nicht verfügbar.");
    }
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(value);
    const fields = Object.fromEntries(
      parts.map((part) => [part.type, part.value]),
    );
    return `${fields.year}-${fields.month}-${fields.day}`;
  }

  _handleEnergyCollectionUpdate(collection) {
    if (!collection.start || !collection.end) {
      return;
    }
    let start;
    let end;
    try {
      start = this._dateInHomeAssistantTimeZone(collection.start);
      end = this._dateInHomeAssistantTimeZone(collection.end);
    } catch (err) {
      this._requestId++;
      this._period = null;
      this._data = null;
      this._loading = false;
      this._error = err.message || "Der Zeitraum konnte nicht gelesen werden.";
      this._render();
      return;
    }
    if (start === this._period?.start && end === this._period?.end) {
      return;
    }
    this._period = { start, end };
    this._data = null;
    this._error = null;
    this._loadData();
  }

  async _loadData() {
    if (!this._hass || !this._period) {
      return;
    }

    const requestId = ++this._requestId;
    this._loading = true;
    this._error = null;
    this._render();

    let data;
    let error;
    try {
      data = await this._hass.callWS({
        type: "energycosttracker/period_data",
        start: `${this._period.start}T00:00:00`,
        end: `${this._period.end}T23:59:59`,
      });
    } catch (err) {
      error = err.message || "Die Daten konnten nicht geladen werden.";
    }

    if (requestId !== this._requestId) {
      return;
    }
    this._data = data || null;
    this._error = error || null;
    this._loading = false;
    this._render();
  }

  _escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (character) => {
      const entities = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      };
      return entities[character];
    });
  }

  _formatEuro(value) {
    if (value === null || value === undefined) {
      return "–";
    }
    return new Intl.NumberFormat(this._hass?.language || undefined, {
      style: "currency",
      currency: "EUR",
    }).format(value);
  }

  _render() {
    if (!this.shadowRoot || !this._config) {
      return;
    }

    const language = this._hass?.language || undefined;
    const granularity = this._data?.granularity || "day";
    const dayView = granularity === "day";
    const monthView = granularity === "month";
    const days = this._data?.days || [];
    const now = new Date();
    const currentDate =
      this._data?.current_date ||
      `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    const chartItems = dayView
      ? days
          .filter((day) => day.date <= currentDate)
          .map((day) => {
            const knownCost = day.known_cost_eur;
            return {
              label: Number(day.date.slice(-2)),
              cost: knownCost,
              payment: day.daily_payment_eur,
              overBudget: day.over_budget,
              title: `${day.date}: bekannte Kosten ${this._formatEuro(knownCost)}`,
            };
          })
      : monthView
        ? (this._data?.months || []).map((monthData) => {
            const monthLabel = new Date(
              monthData.year,
              monthData.month - 1,
              1,
            ).toLocaleDateString(language, {
              month: "short",
              year: "numeric",
            });
            return {
              label: monthLabel,
              cost: monthData.costs_eur,
              payment: monthData.payment_eur,
              overBudget:
                monthData.costs_eur !== null &&
                monthData.payment_eur !== null &&
                monthData.costs_eur > monthData.payment_eur,
              title: `${monthLabel}: bekannte Kosten ${this._formatEuro(monthData.costs_eur)} · ${this._formatEuro(monthData.payment_eur)}`,
            };
          })
        : (this._data?.years || []).map((yearData) => ({
            label: yearData.year,
            cost: yearData.costs_eur,
            payment: yearData.payment_eur,
            overBudget:
              yearData.costs_eur !== null &&
              yearData.payment_eur !== null &&
              yearData.costs_eur > yearData.payment_eur,
            title: `${yearData.year}: bekannte Kosten ${this._formatEuro(yearData.costs_eur)} · ${this._formatEuro(yearData.payment_eur)}`,
          }));
    const rangeLabel = this._period
      ? `${new Date(`${this._period.start}T12:00:00`).toLocaleDateString(language)} – ${new Date(`${this._period.end}T12:00:00`).toLocaleDateString(language)}`
      : "";
    const maxValue = Math.max(
      0.01,
      ...chartItems.flatMap((item) => [item.cost || 0, item.payment || 0]),
    );
    const rawTickStep = maxValue / 4;
    const tickMagnitude = 10 ** Math.floor(Math.log10(rawTickStep));
    const normalizedTickStep = rawTickStep / tickMagnitude;
    const tickMultiplier =
      normalizedTickStep <= 1
        ? 1
        : normalizedTickStep <= 2
          ? 2
          : normalizedTickStep <= 5
            ? 5
            : 10;
    const axisStep = tickMultiplier * tickMagnitude;
    const axisMax = axisStep * 4;
    const axisLabels = Array.from({ length: 5 }, (_, index) => {
      const value = axisStep * (4 - index);
      const position = (index / 4) * 100;
      const edgePosition =
        index === 0 ? "top:0" : index === 4 ? "bottom:0" : `top:${position}%`;
      return `<span style="${edgePosition}">${this._formatEuro(value)}</span>`;
    }).join("");

    const columns = chartItems
      .map((item, index) => {
        const cost = item.cost;
        const payment = item.payment;
        const barHeight =
          cost === null || cost === undefined ? 0 : (cost / axisMax) * 100;
        const allowanceHeight =
          payment === null || payment === undefined
            ? null
            : (payment / axisMax) * 100;
        const title = this._escapeHtml(item.title);
        return `
          <div class="day-column">
            <div class="plot" title="${title}">
              ${
                allowanceHeight === null
                  ? ""
                  : `<div class="allowance" style="bottom:${allowanceHeight}%"></div>`
              }
              <div class="bar ${item.overBudget ? "over-budget" : ""}"
                style="height:${barHeight}%"></div>
            </div>
            <span class="day-label">${!dayView || index % 5 === 0 || index === chartItems.length - 1 ? item.label : ""}</span>
          </div>`;
      })
      .join("");

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 16px; }
        .header, .summary { display: flex; align-items: center; }
        .header { justify-content: space-between; gap: 12px; }
        .title { font-size: 1.2em; font-weight: 500; }
        .period { color: var(--secondary-text-color); text-align: right; }
        @media (max-width: 600px) {
          .header { align-items: flex-start; flex-direction: column; }
          .period { text-align: left; }
        }
        .summary { justify-content: space-around; gap: 12px; margin: 18px 0; text-align: center; }
        .summary-item { display: grid; gap: 4px; }
        .summary-label, .muted { color: var(--secondary-text-color); font-size: 0.9em; }
        .chart-row { display: flex; }
        .y-axis { position: relative; flex: 0 0 56px; height: 210px; padding-right: 6px; box-sizing: border-box; color: var(--secondary-text-color); font-size: 0.7em; text-align: right; }
        .y-axis span { position: absolute; right: 6px; transform: translateY(-50%); white-space: nowrap; }
        .y-axis span:first-child { transform: none; }
        .y-axis span:last-child { transform: translateY(50%); }
        .chart-viewport { flex: 1; min-width: 0; overflow-x: auto; }
        .chart { position: relative; display: flex; align-items: stretch; height: 228px; min-width: 100%; border-bottom: 1px solid var(--divider-color); }
        .day-column { display: flex; flex: 1 0 18px; min-width: 18px; flex-direction: column; }
        .plot { position: relative; flex: 0 0 210px; background: repeating-linear-gradient(to bottom, var(--divider-color) 0, var(--divider-color) 1px, transparent 1px, transparent 25%); }
        .bar { position: absolute; bottom: 0; width: 70%; left: 15%; background: var(--energycosttracker-under-budget-color, #03a9f4); border-radius: 3px 3px 0 0; }
        .bar.over-budget { background: var(--energycosttracker-over-budget-color, #db4437); }
        .allowance { position: absolute; left: 0; right: 0; border-top: 2px solid var(--warning-color, #f4b400); z-index: 1; }
        .day-label { height: 18px; text-align: center; font-size: 0.7em; color: var(--secondary-text-color); }
        .legend { display: flex; justify-content: center; gap: 16px; margin-top: 10px; font-size: 0.85em; }
        .legend-item { display: flex; align-items: center; gap: 5px; }
        .swatch { width: 10px; height: 10px; border-radius: 2px; background: var(--energycosttracker-under-budget-color, #03a9f4); }
        .swatch.over { background: var(--energycosttracker-over-budget-color, #db4437); }
        .line-swatch { width: 14px; border-top: 2px solid var(--warning-color, #f4b400); }
        .message { padding: 20px 0; text-align: center; }
      </style>
      <ha-card>
        <div class="header">
          <div class="title">${this._escapeHtml(this._config.title || "Stromkosten")}</div>
          <div class="period">${this._escapeHtml(rangeLabel)}</div>
        </div>
        ${
          this._loading
            ? '<div class="message">Lade Daten …</div>'
            : this._error
              ? `<div class="message">${this._escapeHtml(this._error)}</div>`
              : this._data
                ? `
                  <div class="summary">
                    <div class="summary-item"><span class="summary-label">Kosten im Zeitraum bis heute</span><strong>${this._formatEuro(this._data.costs_to_date_eur)}</strong></div>
                    <div class="summary-item"><span class="summary-label">Abschlag im Zeitraum bis heute</span><strong>${this._formatEuro(this._data.payment_to_date_eur)}</strong></div>
                    <div class="summary-item"><span class="summary-label">Saldo im Zeitraum</span><strong>${this._formatEuro(this._data.balance_to_date_eur)}</strong></div>
                  </div>
                  <div class="chart-row">
                    <div class="y-axis">${axisLabels}</div>
                    <div class="chart-viewport"><div class="chart">${columns}</div></div>
                  </div>
                  <div class="legend">
                    <span class="legend-item"><span class="swatch"></span>${dayView ? "Bekannte Tageskosten" : monthView ? "Bekannte Monatskosten" : "Bekannte Jahreskosten"}</span>
                    <span class="legend-item"><span class="swatch over"></span>Über Abschlag</span>
                    <span class="legend-item"><span class="line-swatch"></span>${dayView ? "Tagesabschlag" : monthView ? "Monatsabschlag" : "Jahresabschlag"}</span>
                  </div>`
                : this._period
                  ? '<div class="message">Keine Daten für den gewählten Zeitraum verfügbar.</div>'
                  : '<div class="message">Warte auf die EnergyCollection. Prüfen Sie collection_key.</div>'
        }
      </ha-card>`;
  }
}

class EnergyCostTrackerCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
  }

  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  _render() {
    if (!this._config) {
      return;
    }
    const collectionKey = this._config.collection_key || "energy_dashboard";
    const configuredWidth = this._config.width;
    const width =
      typeof configuredWidth === "number" &&
      Number.isFinite(configuredWidth) &&
      configuredWidth >= 10 &&
      configuredWidth <= 100
        ? configuredWidth
        : 100;
    const underBudgetColor = /^#[0-9a-f]{6}$/i.test(
      this._config.under_budget_color || "",
    )
      ? this._config.under_budget_color
      : "#03a9f4";
    const overBudgetColor = /^#[0-9a-f]{6}$/i.test(
      this._config.over_budget_color || "",
    )
      ? this._config.over_budget_color
      : "#db4437";
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .fields { display: grid; gap: 16px; }
        label { display: grid; gap: 6px; color: var(--primary-text-color); }
        input { box-sizing: border-box; color: var(--primary-text-color); font: inherit; }
        input[type="text"], input[type="number"] {
          width: 100%;
          padding: 8px;
          border: 1px solid var(--divider-color);
          border-radius: 4px;
          background: var(--card-background-color);
        }
        input[type="color"] { width: 56px; height: 40px; padding: 2px; }
      </style>
      <div class="fields">
        <label>
          Kartenbreite (%)
          <input class="width" type="number" min="10" max="100" step="5" required value="${width}">
        </label>
        <label>
          Energiekarten-Schlüssel
          <input class="collection-key" type="text" value="${this._escapeHtml(collectionKey)}">
        </label>
        <label>
          Farbe unter Abschlag
          <input class="under-budget-color" type="color" value="${underBudgetColor}">
        </label>
        <label>
          Farbe über Abschlag
          <input class="over-budget-color" type="color" value="${overBudgetColor}">
        </label>
      </div>`;

    this.shadowRoot.querySelector(".width").addEventListener("change", (event) => {
      const input = event.target;
      if (!input.reportValidity()) {
        return;
      }
      this._emitConfig({ width: Number(input.value) });
    });
    this.shadowRoot
      .querySelector(".collection-key")
      .addEventListener("change", (event) => {
        const input = event.target;
        input.setCustomValidity(
          input.value.startsWith("energy_")
            ? ""
            : "Der Schlüssel muss mit 'energy_' beginnen.",
        );
        if (!input.reportValidity()) {
          return;
        }
        this._emitConfig({ collection_key: input.value });
      });
    for (const [selector, key] of [
      [".under-budget-color", "under_budget_color"],
      [".over-budget-color", "over_budget_color"],
    ]) {
      this.shadowRoot.querySelector(selector).addEventListener("change", (event) => {
        this._emitConfig({ [key]: event.target.value });
      });
    }
  }

  _emitConfig(update) {
    this._config = { ...this._config, ...update };
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this._config },
        bubbles: true,
        composed: true,
      }),
    );
  }

  _escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (character) => {
      const entities = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      };
      return entities[character];
    });
  }
}

const CARD_TYPE = "energycosttracker-card";
const EDITOR_TYPE = "energycosttracker-card-editor";

if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, EnergyCostTrackerCard);
}

if (!customElements.get(EDITOR_TYPE)) {
  customElements.define(EDITOR_TYPE, EnergyCostTrackerCardEditor);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === CARD_TYPE)) {
  window.customCards.push({
    type: CARD_TYPE,
    name: "energycosttracker",
    description: "Compares electricity costs with payments over the selected period.",
  });
}