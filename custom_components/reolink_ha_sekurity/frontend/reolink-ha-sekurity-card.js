/**
 * Reolink HA Sekurity — Lovelace Card
 *
 * Compact event timeline with expandable detail,
 * live feed for active events, and segment playback.
 */

const CARD_VERSION = "0.1.14";

class ReolinkHaSekurityCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._events = [];
    this._activeEvents = {};
    this._cameras = [];
    this._selectedCamera = "all";
    this._selectedFilter = "security";
    this._expandedEventId = null;
    this._currentSegmentIndex = 0;
    this._refreshInterval = null;
    this._limit = 25;
    this._offset = 0;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._initialized = true;
      this._fetchEvents();
      this._startAutoRefresh();
    }
    this._updateAlarmStates();
  }

  setConfig(config) {
    this._config = config;
  }

  static getConfigElement() {
    return document.createElement("div");
  }

  static getStubConfig() {
    return {};
  }

  connectedCallback() {
    this._render();
    this._checkDeepLink();
  }

  disconnectedCallback() {
    if (this._refreshInterval) {
      clearInterval(this._refreshInterval);
    }
  }

  // --- Data fetching ---

  async _fetchEvents() {
    if (!this._hass) return;
    try {
      const params = new URLSearchParams({
        camera: this._selectedCamera,
        filter: this._selectedFilter,
        limit: String(this._limit),
        offset: String(this._offset),
      });
      const resp = await this._hass.callApi(
        "GET",
        `reolink_ha_sekurity/events?${params}`
      );
      this._events = resp.events || [];
      this._activeEvents = resp.active_events || {};
      this._cameras = resp.cameras || [];
      this._render();
    } catch (e) {
      if (e.name === "AbortError" || e.message?.includes("AbortError")) {
        console.debug("Fetch events aborted (likely due to rapid navigation)");
        return;
      }
      console.error("Failed to fetch events:", e);
    }
  }

  async _fetchEventDetail(eventId) {
    if (!this._hass) return null;
    try {
      const resp = await this._hass.callApi(
        "GET",
        `reolink_ha_sekurity/event/${eventId}`
      );
      return resp;
    } catch (e) {
      if (e.name === "AbortError" || e.message?.includes("AbortError")) {
        return null;
      }
      console.error("Failed to fetch event detail:", e);
      return null;
    }
  }

  _startAutoRefresh() {
    this._refreshInterval = setInterval(() => this._fetchEvents(), 30000);
  }

  _checkDeepLink() {
    const params = new URLSearchParams(window.location.search);
    const eventId = params.get("event_id");
    if (eventId) {
      this._expandedEventId = eventId;
      const parts = eventId.split('_');
      if (parts.length >= 3) {
        this._selectedCamera = parts.slice(2).join('_');
      }
      this._selectedFilter = "all"; // Ensure we can see the deep-linked event
      this._fetchEvents();
    }
  }

  // --- Alarm state ---

  _updateAlarmStates() {
    if (!this._hass) return;
    const oldFull = this._fullAlarmOn;
    const oldNight = this._nightAlarmOn;

    const fullAlarm = this._hass.states["switch.reolink_ha_sekurity_full_alarm"];
    const nightAlarm = this._hass.states["switch.reolink_ha_sekurity_night_alarm"];
    this._fullAlarmOn = fullAlarm && fullAlarm.state === "on";
    this._nightAlarmOn = nightAlarm && nightAlarm.state === "on";

    if (oldFull !== this._fullAlarmOn || oldNight !== this._nightAlarmOn) {
      this._render();
    }
  }

  async _toggleAlarm(entityId) {
    if (!this._hass) return;
    await this._hass.callService("switch", "toggle", {
      entity_id: entityId,
    });
  }

  // --- Rendering ---

  _render() {
    const style = `
      :host {
        --card-bg: var(--ha-card-background, var(--card-background-color, #1c1c1e));
        --text-primary: var(--primary-text-color, #e5e5e7);
        --text-secondary: var(--secondary-text-color, #8e8e93);
        --accent: var(--primary-color, #0a84ff);
        --danger: #ff453a;
        --success: #30d158;
        --warning: #ff9f0a;
        --surface: var(--ha-card-background, rgba(255,255,255,0.06));
        --border: rgba(255,255,255,0.1);
        --radius: 12px;
      }
      * { box-sizing: border-box; margin: 0; padding: 0; }
      .card {
        background: var(--card-bg);
        border-radius: var(--radius);
        overflow: hidden;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        color: var(--text-primary);
      }
      .header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 16px;
        border-bottom: 1px solid var(--border);
      }
      .header h2 {
        font-size: 16px;
        font-weight: 600;
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .alarm-toggles {
        display: flex;
        gap: 8px;
      }
      .alarm-btn {
        display: flex;
        align-items: center;
        gap: 4px;
        padding: 4px 10px;
        border-radius: 16px;
        border: 1px solid var(--border);
        background: transparent;
        color: var(--text-secondary);
        font-size: 11px;
        font-weight: 500;
        cursor: pointer;
        transition: all 0.2s ease;
      }
      .alarm-btn.active {
        background: var(--danger);
        border-color: var(--danger);
        color: white;
      }
      .alarm-btn .dot {
        width: 6px; height: 6px;
        border-radius: 50%;
        background: var(--text-secondary);
      }
      .alarm-btn.active .dot {
        background: white;
        animation: pulse 2s infinite;
      }
      @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.4; }
      }
      .camera-tabs-container {
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px solid var(--border);
        padding-right: 16px;
      }
      .camera-tabs {
        display: flex;
        gap: 4px;
        padding: 8px 16px;
        overflow-x: auto;
      }
      .filter-tabs {
        display: flex;
        gap: 4px;
      }
      .camera-tab {
        padding: 4px 12px;
        border-radius: 14px;
        border: none;
        background: transparent;
        color: var(--text-secondary);
        font-size: 12px;
        cursor: pointer;
        white-space: nowrap;
        transition: all 0.2s ease;
      }
      .camera-tab.active {
        background: var(--accent);
        color: white;
      }
      .events-list {
        max-height: 600px;
        overflow-y: auto;
      }
      .event-row {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 10px 16px;
        border-bottom: 1px solid var(--border);
        cursor: pointer;
        transition: background 0.15s ease;
      }
      .event-row:hover { background: rgba(255,255,255,0.04); }
      .event-row.active-event {
        border-left: 3px solid var(--danger);
      }
      .event-row.expanded {
        background: rgba(255,255,255,0.04);
        border-bottom: none;
      }
      .event-thumb {
        width: 48px; height: 36px;
        border-radius: 6px;
        background: var(--surface);
        object-fit: cover;
        flex-shrink: 0;
      }
      .event-thumb-placeholder {
        width: 48px; height: 36px;
        border-radius: 6px;
        background: var(--surface);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 18px;
        flex-shrink: 0;
      }
      .event-info {
        flex: 1;
        min-width: 0;
      }
      .event-info .type {
        font-size: 13px;
        font-weight: 500;
      }
      .event-info .camera-name {
        font-size: 11px;
        color: var(--text-secondary);
      }
      .event-badge {
        font-size: 10px;
        padding: 2px 6px;
        border-radius: 8px;
        font-weight: 600;
        text-transform: uppercase;
      }
      .badge-person { background: rgba(255,69,58,0.2); color: var(--danger); }
      .badge-vehicle { background: rgba(0,132,255,0.2); color: var(--accent); }
      .badge-visitor { background: rgba(255,159,10,0.2); color: var(--warning); }
      .badge-motion { background: rgba(142,142,147,0.2); color: var(--text-secondary); }
      .badge-animal { background: rgba(48,209,88,0.2); color: var(--success); }
      .badge-pet { background: rgba(48,209,88,0.2); color: var(--success); }
      .badge-live {
        background: var(--danger);
        color: white;
        animation: pulse 2s infinite;
      }
      .event-time {
        font-size: 11px;
        color: var(--text-secondary);
        text-align: right;
        flex-shrink: 0;
      }
      .event-detail {
        padding: 0 16px 12px 16px;
        border-bottom: 1px solid var(--border);
        background: rgba(255,255,255,0.02);
      }
      .live-feed {
        width: 100%;
        border-radius: 8px;
        background: #000;
        margin-bottom: 8px;
        aspect-ratio: 16/9;
        object-fit: contain;
      }
      .video-player {
        width: 100%;
        border-radius: 8px;
        background: #000;
        margin-bottom: 8px;
        max-height: 300px;
      }
      .segments-bar {
        display: flex;
        gap: 4px;
        flex-wrap: wrap;
        margin-bottom: 8px;
      }
      .seg-btn {
        padding: 2px 8px;
        border-radius: 8px;
        border: 1px solid var(--border);
        background: transparent;
        color: var(--text-secondary);
        font-size: 11px;
        cursor: pointer;
        transition: all 0.15s;
      }
      .seg-btn.active {
        background: var(--accent);
        border-color: var(--accent);
        color: white;
      }
      .seg-btn.writing {
        border-color: var(--warning);
        color: var(--warning);
      }
      .load-more {
        display: block;
        width: 100%;
        padding: 12px;
        border: none;
        background: transparent;
        color: var(--accent);
        font-size: 13px;
        cursor: pointer;
      }
      .load-more:hover { text-decoration: underline; }
      .empty-state {
        padding: 40px 16px;
        text-align: center;
        color: var(--text-secondary);
        font-size: 13px;
      }
      .detail-meta {
        font-size: 11px;
        color: var(--text-secondary);
        margin-bottom: 4px;
      }
    `;

    const eventTypeIcon = (type) => {
      const icons = { person: "🚶", vehicle: "🚗", visitor: "🔔", motion: "👁", animal: "🦌", pet: "🐾" };
      return icons[type] || "👁";
    };

    const eventTypeBadgeClass = (type) => `badge-${type}`;

    const relativeTime = (isoStr) => {
      if (!isoStr) return "";
      const now = Date.now();
      const then = new Date(isoStr).getTime();
      const diffSec = Math.floor((now - then) / 1000);
      if (diffSec < 60) return "just now";
      if (diffSec < 3600) return `${Math.floor(diffSec / 60)} min ago`;
      if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} hr ago`;
      return `${Math.floor(diffSec / 86400)} days ago`;
    };

    // Build camera tabs
    const cameraTabs = ["all", ...this._cameras]
      .map(
        (c) => `<button class="camera-tab ${this._selectedCamera === c ? "active" : ""}"
                  data-camera="${c}">${c === "all" ? "All Cameras" : c.replace(/_/g, " ")}</button>`
      )
      .join("");

    const filterTabs = `
      <button class="camera-tab ${this._selectedFilter === 'all' ? "active" : ""}" data-filter="all">All Events</button>
      <button class="camera-tab ${this._selectedFilter === 'security' ? "active" : ""}" data-filter="security">Security Events</button>
    `;

    // Build event rows
    let eventsHtml = "";
    if (this._events.length === 0) {
      eventsHtml = `<div class="empty-state">No events recorded yet</div>`;
    } else {
      for (const ev of this._events) {
        const isActive = Object.values(this._activeEvents).some(
          (a) => a.event_id === ev.event_id
        );
        const isExpanded = this._expandedEventId === ev.event_id;
        const thumbHtml = ev.snapshot_url
          ? `<img class="event-thumb"
               src="${ev.snapshot_url}"
               alt="" loading="lazy" onerror="this.style.display='none'">`
          : `<div class="event-thumb-placeholder">${eventTypeIcon(ev.event_type)}</div>`;

        eventsHtml += `
          <div class="event-row ${isActive ? "active-event" : ""} ${isExpanded ? "expanded" : ""}"
               data-event-id="${ev.event_id}">
            ${thumbHtml}
            <div class="event-info">
              <div class="type">${eventTypeIcon(ev.event_type)} ${(ev.event_type || "motion").replace(/^\w/, c => c.toUpperCase())}</div>
              <div class="camera-name">${(ev.camera || "").replace(/_/g, " ")}</div>
            </div>
            ${isActive ? '<span class="event-badge badge-live">LIVE</span>' : ""}
            <span class="event-badge ${eventTypeBadgeClass(ev.event_type)}">${ev.event_type || "motion"}</span>
            <div class="event-time">${relativeTime(ev.started_at)}</div>
          </div>
          ${isExpanded ? `<div class="event-detail" id="detail-${ev.event_id}"><div class="empty-state">Loading...</div></div>` : ""}
        `;
      }
    }

    this.shadowRoot.innerHTML = `
      <style>${style}</style>
      <ha-card>
        <div class="card">
          <div class="header">
            <h2>🔒 Reolink HA Sekurity</h2>
            <div class="alarm-toggles">
              <button class="alarm-btn ${this._fullAlarmOn ? "active" : ""}" id="toggle-full">
                <span class="dot"></span>Full
              </button>
              <button class="alarm-btn ${this._nightAlarmOn ? "active" : ""}" id="toggle-night">
                <span class="dot"></span>Night
              </button>
            </div>
          </div>
          <div class="camera-tabs-container">
            <div class="camera-tabs">${cameraTabs}</div>
            <div class="filter-tabs">${filterTabs}</div>
          </div>
          <div class="events-list">${eventsHtml}</div>
          ${this._events.length >= this._limit ? `<button class="load-more">Load more</button>` : ""}
        </div>
      </ha-card>
    `;

    // --- Event listeners ---
    this.shadowRoot.querySelectorAll(".camera-tab[data-camera]").forEach((tab) => {
      tab.addEventListener("click", () => {
        this._selectedCamera = tab.dataset.camera;
        this._offset = 0;
        this._fetchEvents();
      });
    });

    this.shadowRoot.querySelectorAll(".camera-tab[data-filter]").forEach((tab) => {
      tab.addEventListener("click", () => {
        this._selectedFilter = tab.dataset.filter;
        this._offset = 0;
        this._fetchEvents();
      });
    });

    this.shadowRoot.querySelectorAll(".event-row").forEach((row) => {
      row.addEventListener("click", () => {
        const eventId = row.dataset.eventId;
        if (this._expandedEventId === eventId) {
          this._expandedEventId = null;
          this._render();
        } else {
          this._expandedEventId = eventId;
          this._render();
          this._loadEventDetail(eventId);
        }
      });
    });

    const loadMoreBtn = this.shadowRoot.querySelector(".load-more");
    if (loadMoreBtn) {
      loadMoreBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        this._offset += this._limit;
        this._fetchMoreEvents();
      });
    }

    const fullBtn = this.shadowRoot.getElementById("toggle-full");
    if (fullBtn) {
      fullBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        this._toggleAlarm("switch.reolink_ha_sekurity_full_alarm");
      });
    }

    const nightBtn = this.shadowRoot.getElementById("toggle-night");
    if (nightBtn) {
      nightBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        this._toggleAlarm("switch.reolink_ha_sekurity_night_alarm");
      });
    }

    // Auto-load detail for deep-linked event
    if (this._expandedEventId) {
      this._loadEventDetail(this._expandedEventId);
    }
  }

  async _fetchMoreEvents() {
    if (!this._hass) return;
    try {
      const params = new URLSearchParams({
        camera: this._selectedCamera,
        filter: this._selectedFilter,
        limit: String(this._limit),
        offset: String(this._offset),
      });
      const resp = await this._hass.callApi(
        "GET",
        `reolink_ha_sekurity/events?${params}`
      );
      const newEvents = resp.events || [];
      this._events = [...this._events, ...newEvents];
      this._render();
    } catch (e) {
      console.error("Failed to fetch more events:", e);
    }
  }

  async _loadEventDetail(eventId) {
    const detail = await this._fetchEventDetail(eventId);
    if (!detail) return;

    const container = this.shadowRoot.getElementById(`detail-${eventId}`);
    if (!container) return;

    const { metadata, segments, snapshot_url, is_active, camera_entity } = detail;

    let html = "";

    // Live feed for active events
    if (is_active && camera_entity) {
      const proxyUrl = `/api/camera_proxy_stream/${camera_entity}?token=${this._hass.auth.data.access_token}`;
      html += `
        <div class="detail-meta">🔴 LIVE — streaming from ${(metadata.camera || "").replace(/_/g, " ")}</div>
        <img class="live-feed" src="${proxyUrl}" alt="Live feed">
      `;
    }

    // Segment player
    if (segments && segments.length > 0) {
      const firstUrl = segments[0].url;
      html += `<video class="video-player" id="player-${eventId}" controls autoplay playsinline src="${firstUrl}"></video>`;

      // Segment buttons
      html += `<div class="segments-bar">`;
      segments.forEach((seg, i) => {
        html += `<button class="seg-btn ${i === 0 ? "active" : ""}" data-seg-index="${i}" data-seg-url="${seg.url}">▶ ${i + 1}</button>`;
      });

      // Show writing indicator for active events
      if (is_active) {
        html += `<button class="seg-btn writing" disabled>⏳ recording...</button>`;
      }
      html += `</div>`;
    } else if (!is_active) {
      html += `<div class="empty-state">No segments available</div>`;
    }

    // Metadata
    html += `
      <div class="detail-meta">
        Started: ${metadata.started_at ? new Date(metadata.started_at).toLocaleString() : "—"}
        ${metadata.ended_at ? " | Ended: " + new Date(metadata.ended_at).toLocaleString() : ""}
        | Segments: ${(metadata.segments || []).length}
        | Type: ${metadata.event_type}
      </div>
    `;

    container.innerHTML = html;

    // Wire up segment buttons
    container.querySelectorAll(".seg-btn[data-seg-url]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const player = container.querySelector(`#player-${eventId}`);
        if (player) {
          player.src = btn.dataset.segUrl;
          player.play();
        }
        container.querySelectorAll(".seg-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
      });
    });

    // Auto-advance segments
    const player = container.querySelector(`#player-${eventId}`);
    if (player && segments) {
      let currentSeg = 0;
      player.addEventListener("ended", () => {
        currentSeg++;
        if (currentSeg < segments.length) {
          player.src = segments[currentSeg].url;
          player.play();
          container.querySelectorAll(".seg-btn").forEach((b) => b.classList.remove("active"));
          const nextBtn = container.querySelector(`[data-seg-index="${currentSeg}"]`);
          if (nextBtn) nextBtn.classList.add("active");
        } else if (is_active) {
          // Caught up to live — refresh to check for new segments
          setTimeout(() => this._loadEventDetail(eventId), 5000);
        }
      });
    }

    // Auto-refresh for active events
    if (is_active) {
      setTimeout(() => {
        if (this._expandedEventId === eventId) {
          this._loadEventDetail(eventId);
        }
      }, 10000);
    }
  }

  getCardSize() {
    return 5;
  }
}

customElements.define("reolink-ha-sekurity-card", ReolinkHaSekurityCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "reolink-ha-sekurity-card",
  name: "Reolink HA Sekurity",
  description: "Security camera event timeline with alarm controls",
  preview: true,
  documentationURL: "https://github.com/jakubkmiotek/reolink_ha_sekurity",
});

console.info(
  `%c REOLINK-HA-SEKURITY %c v${CARD_VERSION} `,
  "background: #ff453a; color: white; font-weight: bold; padding: 2px 6px; border-radius: 4px 0 0 4px;",
  "background: #1c1c1e; color: #e5e5e7; padding: 2px 6px; border-radius: 0 4px 4px 0;"
);
