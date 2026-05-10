"""Constants for Reolink HA Sekurity."""

DOMAIN = "reolink_ha_sekurity"

# --- Recording defaults ---
DEFAULT_CLIP_DURATION = 30  # seconds per segment
DEFAULT_LOOKBACK = 5  # pre-roll seconds (first segment)
DEFAULT_SEGMENT_OVERLAP = 2  # overlap between segments for stitchability
DEFAULT_POST_ROLL = 15  # seconds to keep recording after sensor turns off
DEFAULT_MERGE_WINDOW = 30  # seconds — re-fire within this window = same event

# --- Storage ---
DEFAULT_MEDIA_PATH = "camera_on_nas/reolink_ha_sekurity"  # relative to /media/
EVENTS_INDEX_FILE = "events.json"
EVENT_METADATA_FILE = "metadata.json"
MAX_EVENTS_INDEX = 500  # max events kept in rolling index per camera
DEFAULT_EVENTS_PAGE_SIZE = 25  # events shown in card by default

# --- Alarm ---
FULL_ALARM_ENTITY = f"switch.{DOMAIN}_full_alarm"
NIGHT_ALARM_ENTITY = f"switch.{DOMAIN}_night_alarm"
DEFAULT_NIGHT_START = "22:00"
DEFAULT_NIGHT_END = "07:00"

# --- Lights ---
DEFAULT_LIGHT_TIMEOUT = 300  # 5 minutes

# --- Detection type priority (higher = more important) ---
EVENT_TYPE_PRIORITY = {
    "motion": 0,
    "animal": 1,
    "pet": 1,
    "vehicle": 2,
    "visitor": 3,
    "person": 4,
}

# --- Config keys ---
CONF_MEDIA_PATH = "media_path"
CONF_NOTIFY_TARGETS = "notify_targets"
CONF_NIGHT_START = "night_start"
CONF_NIGHT_END = "night_end"
CONF_LIGHT_ENTITIES = "light_entities"
CONF_LIGHT_TIMEOUT = "light_timeout"
CONF_DASHBOARD_PATH = "dashboard_path"
CONF_CAMERAS = "cameras"
CONF_CAMERA_ENTITY = "camera_entity"
CONF_CAMERA_NAME = "camera_name"
CONF_TRIGGER_SENSORS = "trigger_sensors" # Deprecated, kept for migration
CONF_RECORD_SENSORS = "record_sensors"
CONF_ALARM_SENSORS = "alarm_sensors"
CONF_CLIP_DURATION = "clip_duration"
CONF_LOOKBACK = "lookback"
CONF_POST_ROLL = "post_roll"
