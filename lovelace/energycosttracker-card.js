class EnergyCostTrackerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._year = new Date().getFullYear();
    this._month = new Date().getMonth() + 1;
    this._view = "month";
    this._requestId = 0;
    this._loading = false;
    this._error = null;
    this._data = null;
    this._availableYears = null;
  }

  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error("Eine gültige Kartenkonfiguration ist erforderlich.");
    }
    this._config = config;
    this._render();
    if (this._hass && !this._data && !this._loading) {
      this._loadData();
    }
  }

  set hass(hass) {
    const isInitialHass = !this._hass;
    this._hass = hass;
    if (isInitialHass && this._config) {
      this._loadData();
    }
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return 5;
  }

  async _loadData() {
    if (!this._hass) {
      return;
    }

    const requestId = ++this._requestId;
    this._loading = true;
    this._error = null;
    this._render();

    let data;
    let error;
    try {
      if (this._availableYears === null) {
        const availableYears = await this._hass.callWS({
          type: "energycosttracker/available_years",
        });
        if (requestId !== this._requestId) {
          return;
        }
        this._availableYears = availableYears;
        if (
          availableYears.length &&
          !availableYears.includes(this._year)
        ) {
          this._year = availableYears[0];
        }
      }
      data = await this._hass.callWS(
        this._view === "year"
          ? {
              type: "energycosttracker/year_data",
              year: this._year,
            }
          : {
              type: "energycosttracker/month_data",
              year: this._year,
              month: this._month,
            },
      );
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
    const yearView = this._view === "year";
    const availableYears = this._availableYears?.length
      ? this._availableYears
      : [this._year];
    const currentYear = this._data?.current_year || new Date().getFullYear();
    const isFutureYear = this._year > currentYear;
    const months = this._data?.months || [];
    const days = this._data?.days || [];
    const now = new Date();
    const currentDate =
      this._data?.current_date ||
      `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    const visibleDays = days.filter(
      (day) => day.date <= currentDate,
    );
    const chartItems = yearView
      ? months.map((monthData) => {
          const isCurrentMonth =
            this._year === this._data.current_year &&
            monthData.month === this._data.current_month;
          const payment = isCurrentMonth
            ? monthData.payment_to_date_eur
            : monthData.payment_eur;
          const monthLabel = new Date(
            this._year,
            monthData.month - 1,
            1,
          ).toLocaleDateString(language, { month: "short" });
          return {
            label: monthLabel,
            cost: monthData.costs_eur,
            payment,
            overBudget:
              monthData.costs_eur !== null &&
              payment !== null &&
              monthData.costs_eur > payment,
            title: `${monthLabel} ${this._year}: bekannte Kosten ${this._formatEuro(monthData.costs_eur)} · ${this._formatEuro(payment)}`,
          };
        })
      : visibleDays.map((day) => {
          const knownCost = day.known_cost_eur;
          return {
            label: Number(day.date.slice(-2)),
            cost: knownCost,
            payment: day.daily_payment_eur,
            overBudget: day.over_budget,
            title: `${day.date}: bekannte Kosten ${this._formatEuro(knownCost)}`,
          };
        });
    const formatSummaryValue = (value) =>
      isFutureYear && yearView ? "–" : this._formatEuro(value);
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
            <span class="day-label">${yearView || index % 5 === 0 || index === chartItems.length - 1 ? item.label : ""}</span>
          </div>`;
      })
      .join("");

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 16px; }
        .header, .selectors, .summary { display: flex; align-items: center; }
        .header { justify-content: space-between; gap: 12px; }
        .title { font-size: 1.2em; font-weight: 500; }
        .selectors { gap: 8px; }
        .selectors select { max-width: 10em; }
        @media (max-width: 600px) {
          .header { align-items: flex-start; flex-direction: column; }
          .selectors { width: 100%; flex-wrap: wrap; }
          .selectors select { flex: 1 1 0; min-width: 0; max-width: none; }
        }
        select { color: var(--primary-text-color); background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 4px; padding: 6px; }
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
        .bar { position: absolute; bottom: 0; width: 70%; left: 15%; background: var(--info-color, #03a9f4); border-radius: 3px 3px 0 0; }
        .bar.over-budget { background: var(--error-color, #db4437); }
        .allowance { position: absolute; left: 0; right: 0; border-top: 2px solid var(--warning-color, #f4b400); z-index: 1; }
        .day-label { height: 18px; text-align: center; font-size: 0.7em; color: var(--secondary-text-color); }
        .legend { display: flex; justify-content: center; gap: 16px; margin-top: 10px; font-size: 0.85em; }
        .legend-item { display: flex; align-items: center; gap: 5px; }
        .swatch { width: 10px; height: 10px; border-radius: 2px; background: var(--info-color, #03a9f4); }
        .swatch.over { background: var(--error-color, #db4437); }
        .line-swatch { width: 14px; border-top: 2px solid var(--warning-color, #f4b400); }
        .message { padding: 20px 0; text-align: center; }
      </style>
      <ha-card>
        <div class="header">
          <div class="title">${this._escapeHtml(this._config.title || "Stromkosten")}</div>
          <div class="selectors">
            <select class="view" aria-label="Ansicht">
              <option value="month" ${!yearView ? "selected" : ""}>Monat</option>
              <option value="year" ${yearView ? "selected" : ""}>Jahr</option>
            </select>
            ${
              yearView
                ? ""
                : `<select class="month" aria-label="Monat">
                    ${Array.from({ length: 12 }, (_, index) => {
                      const month = index + 1;
                      const label = new Date(2000, index, 1).toLocaleDateString(language, {
                        month: "long",
                      });
                      return `<option value="${month}" ${month === this._month ? "selected" : ""}>${label}</option>`;
                    }).join("")}
                  </select>`
            }
            <select class="year" aria-label="Jahr" ${this._availableYears?.length === 0 ? "disabled" : ""}>
              ${availableYears
                .map(
                  (year) =>
                    `<option value="${year}" ${year === this._year ? "selected" : ""}>${year}</option>`,
                )
                .join("")}
            </select>
          </div>
        </div>
        ${
          this._loading
            ? '<div class="message">Lade Monatsdaten …</div>'
            : this._error
              ? `<div class="message">${this._escapeHtml(this._error)}</div>`
              : this._data
                ? `
                  <div class="summary">
                    <div class="summary-item"><span class="summary-label">Kosten bis heute</span><strong>${formatSummaryValue(this._data.costs_to_date_eur)}</strong></div>
                    <div class="summary-item"><span class="summary-label">Abschlag bis heute</span><strong>${formatSummaryValue(this._data.payment_to_date_eur)}</strong></div>
                    <div class="summary-item"><span class="summary-label">Saldo offen</span><strong>${formatSummaryValue(this._data.balance_to_date_eur)}</strong></div>
                  </div>
                  <div class="chart-row">
                    <div class="y-axis">${axisLabels}</div>
                    <div class="chart-viewport"><div class="chart">${columns}</div></div>
                  </div>
                  <div class="legend">
                    <span class="legend-item"><span class="swatch"></span>${yearView ? "Bekannte Monatskosten" : "Bekannte Tageskosten"}</span>
                    <span class="legend-item"><span class="swatch over"></span>Über Abschlag</span>
                    <span class="legend-item"><span class="line-swatch"></span>${yearView ? "Monatsabschlag" : "Tagesabschlag"}</span>
                  </div>`
                : '<div class="message">Keine Monatsdaten verfügbar.</div>'
        }
      </ha-card>`;

    this.shadowRoot.querySelector(".view")?.addEventListener("change", (event) => {
      this._view = event.target.value;
      this._data = null;
      this._loadData();
    });
    this.shadowRoot.querySelector(".month")?.addEventListener("change", (event) => {
      this._month = Number(event.target.value);
      this._loadData();
    });
    this.shadowRoot.querySelector(".year")?.addEventListener("change", (event) => {
      const year = Number(event.target.value);
      if (this._availableYears?.includes(year)) {
        this._year = year;
        this._loadData();
      }
    });
  }
}

const CARD_TYPE = "energycosttracker-card";

if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, EnergyCostTrackerCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === CARD_TYPE)) {
  window.customCards.push({
    type: CARD_TYPE,
    name: "energycosttracker",
    description: "Compares daily electricity costs with the payment amount.",
  });
}