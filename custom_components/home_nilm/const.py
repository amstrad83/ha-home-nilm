"""Constants for Home NILM."""
from __future__ import annotations

DOMAIN = "home_nilm"
PLATFORMS = ["sensor", "select", "text"]

# configuration keys
CONF_GRID_POWER = "grid_power"
CONF_PV_POWER = "pv_power"
CONF_SUBMETERS = "submeters"
CONF_REACTIVE_POWER = "reactive_power"
CONF_POWER_FACTOR = "power_factor"
CONF_INVERT_REACTIVE = "invert_reactive"
CONF_VARIABLE_ENTITIES = "variable_entities"
CONF_VARIABLE_NAME = "variable_name"
CONF_VARIABLE_OFF_DELAY = "variable_off_delay"

# engine tuning keys (mirror EngineConfig field names)
CONF_STEP_THRESHOLD = "step_threshold"
CONF_PERSISTENCE = "persistence_s"
CONF_MATCH_SIGMA = "match_sigma"
CONF_SUSTAINED_POWER = "sustained_power"
CONF_SUSTAINED_MINUTES = "sustained_minutes"
CONF_HOUR_WEIGHT = "hour_weight"

DEFAULTS = {
    CONF_STEP_THRESHOLD: 80,
    CONF_PERSISTENCE: 10,
    CONF_MATCH_SIGMA: 3.0,
    CONF_SUSTAINED_POWER: 150,
    CONF_SUSTAINED_MINUTES: 15,
    CONF_HOUR_WEIGHT: 0.5,
    CONF_VARIABLE_NAME: "Variable loads",
    CONF_VARIABLE_OFF_DELAY: 300,
    CONF_INVERT_REACTIVE: False,
}

# states that mean "this variable load is not running"
INACTIVE_STATES = {"off", "idle", "standby", "unavailable", "unknown", "none", ""}

STORAGE_KEY = DOMAIN
SAVE_DELAY = 60
TICK_SECONDS = 5

EVENT_UNKNOWN_LOAD = f"{DOMAIN}_unknown_load"
SIGNAL_UPDATE = f"{DOMAIN}_update_{{}}"
SIGNAL_SIGNATURE_ADDED = f"{DOMAIN}_signature_added_{{}}"

NOW_OPTION = "now"
NONE_OPTION = "none"

ATTR_EVENT_ID = "event_id"
ATTR_NAME = "name"
ATTR_NEW_NAME = "new_name"
ATTR_POWER = "power"
ATTR_REACTIVE_RATIO = "reactive_ratio"
ATTR_DURATION = "duration_minutes"
ATTR_HOURS = "hours"
ATTR_SAMPLES = "samples"
ATTR_ENTRY_ID = "entry_id"
