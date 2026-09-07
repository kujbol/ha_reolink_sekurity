/**
 * Reolink HA Sekurity — Lovelace Card
 *
 * Compact event timeline with expandable detail,
 * live feed for active events, and segment playback.
 */

const CARD_VERSION = "0.2.16";

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
    this._lastProcessedDeepLink = null;
    this._currentSegmentIndex = 0;
    this._refreshInterval = null;
    this._limit = 25;
    this._offset = 0;
  }

  _updateUrlParam(eventId) {
    try {
      const url = new URL(window.location.href);
      if (eventId) {
        url.searchParams.set("event_id", eventId);
      } else {
        url.searchParams.delete("event_id");
      }
      window.history.replaceState(null, "", url.toString());
    } catch (_) {
      // Ignore if URL manipulation fails in restricted environments
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._initialized = true;
      this._checkDeepLink();
      if (!this._expandedEventId) {
        this._fetchEvents();
      }
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

    this._onLocationChange = () => this._checkDeepLink();
    window.addEventListener("location-changed", this._onLocationChange);
    window.addEventListener("popstate", this._onLocationChange);
  }

  disconnectedCallback() {
    if (this._refreshInterval) {
      clearInterval(this._refreshInterval);
    }
    if (this._onLocationChange) {
      window.removeEventListener("location-changed", this._onLocationChange);
      window.removeEventListener("popstate", this._onLocationChange);
    }
  }

  // --- Data fetching ---

  async _fetchEvents(forceRender = false) {
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
      
      // Do not re-render if an event is currently expanded to prevent interrupting playback,
      // unless forceRender is true (needed for initial load and deep link changes).
      if (!this._expandedEventId || forceRender) {
        this._render();
      }
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
    if (this._hass) {
      this._hass.callService("system_log", "write", {
        level: "warning",
        message: `[SEKURITY-CARD] _checkDeepLink: href=${window.location.href}, search=${window.location.search}, hash=${window.location.hash}`
      });
    }
    const params = new URLSearchParams(window.location.search);
    const eventId = params.get("event_id");
    if (eventId && this._lastProcessedDeepLink !== eventId) {
      this._lastProcessedDeepLink = eventId;
      if (this._expandedEventId !== eventId) {
        this._expandedEventId = eventId;
        const parts = eventId.split('_');
        if (parts.length >= 3) {
          this._selectedCamera = parts.slice(2).join('_');
        }
        this._selectedFilter = "all"; // Ensure we can see the deep-linked event
        this._fetchEvents(true); // Force render for deep links
      }
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
    const prevList = this.shadowRoot.querySelector(".events-list");
    const savedScrollTop = prevList ? prevList.scrollTop : null;

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
        flex-wrap: wrap;
        gap: 6px;
        padding: 8px 16px;
      }
      .filter-tabs {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
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
        padding: 12px 16px;
        border-bottom: 1px solid var(--border);
        background: rgba(255,255,255,0.02);
      }
      .media-grid {
        display: grid;
        grid-template-columns: 1fr;
        gap: 8px;
        margin-bottom: 8px;
      }
      @media (min-width: 600px) {
        .media-grid {
          grid-template-columns: 1fr 1fr;
        }
      }
      .media-panel {
        position: relative;
        min-width: 0;
      }
      .media-panel-label {
        font-size: 10px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        color: var(--text-secondary);
        margin-bottom: 4px;
      }
      .media-panel-label.live-label {
        color: var(--danger);
      }
      .live-feed {
        width: 100%;
        border-radius: 8px;
        background: #000;
        display: block;
        max-height: 300px;
        object-fit: contain;
      }
      .video-player-container {
        position: relative;
        width: 100%;
        border-radius: 8px;
        overflow: hidden;
        background: #000;
        max-height: 300px;
      }
      .video-player-container video {
        width: 100%;
        display: block;
        max-height: 300px;
        background: #000;
      }
      .video-player-container video.hidden-preload {
        position: absolute;
        top: 0; left: 0;
        width: 100%; height: 100%;
        opacity: 0;
        pointer-events: none;
        z-index: 0;
      }
      .video-player-container video.active-player {
        position: relative;
        z-index: 1;
      }
      .timeline-bar {
        position: relative;
        width: 100%;
        height: 24px;
        background: rgba(255,255,255,0.08);
        border-radius: 12px;
        margin: 8px 0;
        cursor: pointer;
        overflow: hidden;
        touch-action: none;
        user-select: none;
        -webkit-user-select: none;
      }
      .timeline-progress {
        height: 100%;
        background: var(--accent);
        border-radius: 12px;
        transition: width 0.1s linear;
        min-width: 2px;
        pointer-events: none;
      }
      .timeline-handle {
        position: absolute;
        top: 50%;
        transform: translate(-50%, -50%);
        width: 16px;
        height: 16px;
        background: #ffffff;
        border: 2px solid var(--accent);
        border-radius: 50%;
        box-shadow: 0 2px 4px rgba(0,0,0,0.4);
        pointer-events: none;
        transition: left 0.1s linear;
        z-index: 2;
      }
      .timeline-tick {
        position: absolute;
        top: 0;
        width: 1px;
        height: 100%;
        background: rgba(255,255,255,0.15);
        pointer-events: none;
      }
      .timeline-info {
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 10px;
        color: var(--text-secondary);
        padding: 0 2px;
      }
      .timeline-info .segment-indicator {
        opacity: 0.6;
      }
      .recording-pulse {
        display: inline-block;
        width: 6px; height: 6px;
        border-radius: 50%;
        background: var(--warning);
        animation: pulse 2s infinite;
        margin-left: 4px;
        vertical-align: middle;
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

    const newList = this.shadowRoot.querySelector(".events-list");
    if (newList && savedScrollTop !== null) {
      newList.scrollTop = savedScrollTop;
      requestAnimationFrame(() => {
        if (newList) newList.scrollTop = savedScrollTop;
      });
    }

    // --- Event listeners ---
    this.shadowRoot.querySelectorAll(".camera-tab[data-camera]").forEach((tab) => {
      tab.addEventListener("click", (e) => {
        e.stopPropagation();
        this._selectedCamera = tab.dataset.camera;
        this._offset = 0;
        this._fetchEvents();
      });
    });

    this.shadowRoot.querySelectorAll(".camera-tab[data-filter]").forEach((tab) => {
      tab.addEventListener("click", (e) => {
        e.stopPropagation();
        this._selectedFilter = tab.dataset.filter;
        this._offset = 0;
        this._fetchEvents();
      });
    });

    this.shadowRoot.querySelectorAll(".event-row").forEach((row) => {
      row.addEventListener("click", (e) => {
        e.stopPropagation();
        const eventId = row.dataset.eventId;
        if (this._expandedEventId === eventId) {
          this._expandedEventId = null;
          this._lastProcessedDeepLink = null;
          this._updateUrlParam(null);
          this._render();
        } else {
          this._expandedEventId = eventId;
          this._lastProcessedDeepLink = eventId;
          this._updateUrlParam(eventId);
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

    const { metadata, stream_url, segments, snapshot_url, is_active, camera_entity } = detail;

    let html = "";

    const hasLive = is_active && camera_entity;
    const hasSegments = (segments && segments.length > 0) || !!stream_url;

    // Build media panels
    if (hasLive || hasSegments) {
      html += `<div class="media-grid">`;

      // Live feed panel
      if (hasLive) {
        html += `
          <div class="media-panel">
            <div class="media-panel-label live-label">🔴 Live — ${(metadata.camera || "").replace(/_/g, " ")}</div>
            <ha-camera-stream id="live-${eventId}" allow-exoplayer="true" muted="true" class="live-feed"></ha-camera-stream>
          </div>
        `;
      }

      // Recorded segments panel — seamless player
      if (hasSegments) {
        const totalDuration = (segments && segments.length > 0)
          ? segments.reduce((sum, s) => sum + (s.duration || 30), 0)
          : 30;
        const videoSrc = stream_url || (segments && segments[0] ? segments[0].url : "");

        // Build timeline ticks for segment boundaries
        let ticksHtml = '';
        if (segments && segments.length > 1 && !stream_url) {
          let accum = 0;
          for (let i = 1; i < segments.length; i++) {
            accum += segments[i - 1].duration || 30;
            const pct = (accum / totalDuration) * 100;
            ticksHtml += `<div class="timeline-tick" style="left:${pct}%"></div>`;
          }
        }

        html += `
          <div class="media-panel">
            <div class="media-panel-label">▶ Recording${is_active ? ' <span class="recording-pulse"></span>' : ''}</div>
            <div class="video-player-container" id="vpc-${eventId}">
              <video class="active-player" id="player-${eventId}" controls autoplay playsinline src="${videoSrc}"></video>
              ${stream_url ? "" : `<video class="hidden-preload" id="preload-${eventId}" preload="auto" playsinline></video>`}
            </div>
            <div class="timeline-bar" id="timeline-${eventId}">
              ${ticksHtml}
              <div class="timeline-progress" id="progress-${eventId}" style="width:0%"></div>
              <div class="timeline-handle" id="handle-${eventId}" style="left:0%"></div>
            </div>
            <div class="timeline-info">
              <span id="time-current-${eventId}">0:00</span>
              <span class="segment-indicator" id="seg-info-${eventId}">${stream_url ? "Continuous Stream" : `Segment 1/${segments ? segments.length : 1}`}</span>
              <span id="time-total-${eventId}">${Math.floor(totalDuration / 60)}:${String(Math.floor(totalDuration % 60)).padStart(2, '0')}</span>
            </div>
          </div>
        `;
      } else if (hasLive) {
        // Active event but no segments yet — show placeholder
        html += `
          <div class="media-panel">
            <div class="media-panel-label">▶ Recording</div>
            <div class="empty-state">Recording in progress…</div>
          </div>
        `;
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

    // Bind ha-camera-stream properties
    const streamEl = container.querySelector(`#live-${eventId}`);
    if (streamEl && camera_entity) {
      streamEl.hass = this._hass;
      streamEl.stateObj = this._hass.states[camera_entity];
    }

    // Wire up seamless segment playback with preloading
    const player = container.querySelector(`#player-${eventId}`);
    const preloadEl = container.querySelector(`#preload-${eventId}`);
    const progressBar = container.querySelector(`#progress-${eventId}`);
    const handlePin = container.querySelector(`#handle-${eventId}`);
    const timeCurrentEl = container.querySelector(`#time-current-${eventId}`);
    const segInfoEl = container.querySelector(`#seg-info-${eventId}`);
    const timelineBar = container.querySelector(`#timeline-${eventId}`);
    const videoContainer = container.querySelector(`#vpc-${eventId}`);

    if (videoContainer) {
      ["touchstart", "touchmove", "pointerdown", "pointermove", "click"].forEach((evtName) => {
        videoContainer.addEventListener(evtName, (e) => e.stopPropagation());
      });
    }

    if (player && (stream_url || (segments && segments.length > 0))) {
      let currentSeg = 0;
      const totalDuration = (segments && segments.length > 0)
        ? segments.reduce((sum, s) => sum + (s.duration || 30), 0)
        : 30;

      // Calculate cumulative start times for each segment
      const segStartTimes = [0];
      if (segments && segments.length > 0) {
        for (let i = 1; i < segments.length; i++) {
          segStartTimes.push(segStartTimes[i - 1] + (segments[i - 1].duration || 30));
        }
      }

      // Preload next segment
      const preloadNext = () => {
        if (!stream_url && preloadEl && segments && currentSeg + 1 < segments.length) {
          preloadEl.src = segments[currentSeg + 1].url;
          preloadEl.load();
        }
      };

      // Format time as M:SS
      const fmtTime = (sec) => {
        const m = Math.floor(sec / 60);
        const s = Math.floor(sec % 60);
        return `${m}:${String(s).padStart(2, '0')}`;
      };

      // Update timeline progress
      const updateProgress = () => {
        if (!player || player.paused && player.ended) return;
        const globalTime = stream_url
          ? (player.currentTime || 0)
          : (segStartTimes[currentSeg] || 0) + (player.currentTime || 0);
        const maxTime = stream_url ? (player.duration || totalDuration) : totalDuration;
        const pct = Math.min((globalTime / maxTime) * 100, 100);
        if (progressBar) progressBar.style.width = `${pct}%`;
        if (handlePin) handlePin.style.left = `${pct}%`;
        if (timeCurrentEl) timeCurrentEl.textContent = fmtTime(globalTime);
        if (segInfoEl) {
          segInfoEl.textContent = stream_url
            ? "Continuous Stream"
            : `Segment ${currentSeg + 1}/${segments ? segments.length : 1}`;
        }
      };

      player.addEventListener('timeupdate', updateProgress);
      player.addEventListener('loadeddata', () => {
        preloadNext();
        updateProgress();
      });

      // Preload first next segment
      preloadNext();

      // Segment transition or completion
      player.addEventListener('ended', () => {
        if (stream_url) return;
        currentSeg++;
        if (segments && currentSeg < segments.length) {
          // Swap: use the preloaded source
          player.src = segments[currentSeg].url;
          player.play().catch(() => {});
          // Preload the one after that
          preloadNext();
        } else if (is_active) {
          // Caught up to live — refresh to check for new segments
          setTimeout(() => this._loadEventDetail(eventId), 5000);
        }
      });

      // Allow clicking and touch dragging on timeline to seek smoothly
      if (timelineBar) {
        let isDragging = false;

        const seekToClientX = (clientX) => {
          const rect = timelineBar.getBoundingClientRect();
          if (!rect.width) return;
          const clickPct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
          const maxDuration = stream_url ? (player.duration || totalDuration) : totalDuration;
          const targetTime = clickPct * maxDuration;

          // Update UI immediately during drag
          if (progressBar) progressBar.style.width = `${clickPct * 100}%`;
          if (handlePin) handlePin.style.left = `${clickPct * 100}%`;
          if (timeCurrentEl) timeCurrentEl.textContent = fmtTime(targetTime);

          if (stream_url) {
            player.currentTime = targetTime;
            if (player.paused && !isDragging) player.play().catch(() => {});
            return;
          }

          // Find which segment targetTime falls into
          let targetSeg = 0;
          if (segments && segments.length > 0) {
            for (let i = segments.length - 1; i >= 0; i--) {
              if (targetTime >= segStartTimes[i]) {
                targetSeg = i;
                break;
              }
            }
          }

          const segLocalTime = targetTime - (segStartTimes[targetSeg] || 0);

          if (targetSeg !== currentSeg && segments && segments[targetSeg]) {
            currentSeg = targetSeg;
            player.src = segments[currentSeg].url;
            player.addEventListener('loadeddata', function seekOnLoad() {
              player.currentTime = segLocalTime;
              if (player.paused && !isDragging) player.play().catch(() => {});
              player.removeEventListener('loadeddata', seekOnLoad);
            });
          } else {
            player.currentTime = segLocalTime;
          }

          preloadNext();
        };

        const handlePointerDown = (e) => {
          e.stopPropagation();
          if (e.cancelable) e.preventDefault();
          isDragging = true;
          if (timelineBar.setPointerCapture && e.pointerId !== undefined) {
            try { timelineBar.setPointerCapture(e.pointerId); } catch (_) {}
          }
          seekToClientX(e.clientX);
        };

        const handlePointerMove = (e) => {
          if (!isDragging) return;
          e.stopPropagation();
          if (e.cancelable) e.preventDefault();
          seekToClientX(e.clientX);
        };

        const handlePointerUp = (e) => {
          if (!isDragging) return;
          e.stopPropagation();
          if (e.cancelable) e.preventDefault();
          isDragging = false;
          if (timelineBar.releasePointerCapture && e.pointerId !== undefined) {
            try { timelineBar.releasePointerCapture(e.pointerId); } catch (_) {}
          }
          if (player.paused) player.play().catch(() => {});
        };

        timelineBar.addEventListener('pointerdown', handlePointerDown);
        timelineBar.addEventListener('pointermove', handlePointerMove);
        timelineBar.addEventListener('pointerup', handlePointerUp);
        timelineBar.addEventListener('pointercancel', handlePointerUp);

        // Touch event fallbacks for touchscreens where pointer events might be passive
        timelineBar.addEventListener('touchstart', (e) => {
          e.stopPropagation();
          if (e.touches && e.touches.length > 0) {
            handlePointerDown({
              clientX: e.touches[0].clientX,
              stopPropagation: () => e.stopPropagation(),
              preventDefault: () => { if (e.cancelable) e.preventDefault(); }
            });
          }
        }, { passive: false });

        timelineBar.addEventListener('touchmove', (e) => {
          e.stopPropagation();
          if (e.cancelable) e.preventDefault();
          if (isDragging && e.touches && e.touches.length > 0) {
            seekToClientX(e.touches[0].clientX);
          }
        }, { passive: false });

        timelineBar.addEventListener('touchend', (e) => {
          e.stopPropagation();
          if (isDragging) {
            isDragging = false;
            if (player.paused) player.play().catch(() => {});
          }
        });
      }

      // Smart seek: start from when the interesting detection occurred
      // Priority: type_upgraded_at > lookback > beginning
      const smartSeek = () => {
        let seekOffset = 0; // seconds from recording start

        if (metadata.type_upgraded_at && metadata.started_at) {
          // Event was upgraded (e.g., motion→person).
          // Seek to when the upgrade happened.
          const startedMs = new Date(metadata.started_at).getTime();
          const upgradedMs = new Date(metadata.type_upgraded_at).getTime();
          seekOffset = Math.max(0, (upgradedMs - startedMs) / 1000);
          // Back up 2 seconds so user sees the person appearing
          seekOffset = Math.max(0, seekOffset - 2);
        } else if (metadata.lookback && metadata.lookback > 0) {
          // No upgrade, but has lookback pre-roll — skip past it
          seekOffset = metadata.lookback;
        }

        if (seekOffset <= 0) return;

        if (stream_url) {
          player.addEventListener('loadeddata', function seekOnLoad() {
            if (player.currentTime < 1) {
              const skipTo = Math.min(seekOffset, (player.duration || totalDuration) - 1);
              if (skipTo > 0) player.currentTime = skipTo;
            }
            player.removeEventListener('loadeddata', seekOnLoad);
          });
          return;
        }

        // Find which segment this offset falls into
        let targetSeg = 0;
        if (segments && segments.length > 0) {
          for (let i = segments.length - 1; i >= 0; i--) {
            if (seekOffset >= segStartTimes[i]) {
              targetSeg = i;
              break;
            }
          }
        }

        const segLocalTime = seekOffset - (segStartTimes[targetSeg] || 0);

        if (targetSeg === 0) {
          // Same first segment — just seek within it
          player.addEventListener('loadeddata', function seekOnLoad() {
            if (player.currentTime < 1) {
              const skipTo = Math.min(segLocalTime, player.duration - 1);
              if (skipTo > 0) player.currentTime = skipTo;
            }
            player.removeEventListener('loadeddata', seekOnLoad);
          });
        } else {
          // Different segment — switch to it and seek
          currentSeg = targetSeg;
          player.src = segments[currentSeg].url;
          player.addEventListener('loadeddata', function seekOnLoad() {
            const skipTo = Math.min(segLocalTime, player.duration - 1);
            if (skipTo > 0) player.currentTime = skipTo;
            player.play().catch(() => {});
            player.removeEventListener('loadeddata', seekOnLoad);
          });
          preloadNext();
        }
      };
      smartSeek();
    }
  }

  getCardSize() {
    return 5;
  }
}

if (!customElements.get("reolink-ha-sekurity-card")) {
  customElements.define("reolink-ha-sekurity-card", ReolinkHaSekurityCard);
}

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
