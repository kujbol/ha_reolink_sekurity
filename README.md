# Reolink HA Sekurity 🔒

A lightweight, HACS-compatible Home Assistant custom integration that provides Frigate-like NVR functionality for Reolink cameras — without the complexity.

## Features

- **Event-driven recording** — Records clips when person/vehicle/motion is detected
- **Continuous segmented recording** — Records as long as detection is active, split into stitchable 30s segments with pre-roll and post-roll
- **Dual alarm system** — Full Alarm (24/7) and Night Alarm (configurable hours) with `input_boolean` toggles
- **Per-camera alarm override** — Doorbell/busy cameras can record without triggering notifications
- **Push notifications** — High-priority alerts on iOS (critical) and Android with deep-links to clips
- **Outside light activation** — Turns on configured lights when alarm is triggered
- **Lovelace dashboard card** — Event timeline with camera filters, alarm toggles, live feed for active events, and inline clip playback
- **JSON metadata** — No database needed, everything stored as simple JSON files on your NAS
- **Easy camera management** — Config flow with auto-discovery of Reolink sensors

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant
2. Click the three-dot menu → **Custom repositories**
3. Add this repository URL and select **Integration** as the category
4. Click **Download**
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/reolink_ha_sekurity` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Reolink HA Sekurity**
3. **Step 1**: Configure global settings (media path, notification targets, night hours, lights)
4. **Step 2**: Add your cameras (select camera entity, pick detection sensors)
5. Add the Lovelace card to your dashboard

### Prerequisites

- Reolink cameras already set up via the official [Reolink integration](https://www.home-assistant.io/integrations/reolink/)
- **Enable "Preload stream"** for each camera entity (Settings → Devices → Reolink → Camera → ⚙️ → Preload stream)
- NAS mounted as media storage in Home Assistant

### Dashboard Card

Add to any dashboard via the UI card picker, or manually:

```yaml
type: custom:reolink-ha-sekurity-card
```

## Storage Structure

```
/media/camera_on_nas/reolink_ha_sekurity/
├── front_door/
│   ├── events.json
│   └── 20260503_200644_front_door/
│       ├── metadata.json
│       ├── snapshot.jpg
│       └── 20260503_200644_front_door_seg001.mp4
└── backyard/
    └── ...
```

## License

MIT
