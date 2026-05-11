SERIAL_PORT = "COM5"
BAUD_RATE   = 1_500_000

# Normalized spatial centroids for each channel (-1..+1)
# X: negative = left, positive = right
# Y: negative = heel, positive = toes
CH_POSITIONS = {
    0: (-0.5, +0.5),   # front-left  (left toes)
    1: (-0.5, -0.5),   # back-left   (left heel)
    2: (+0.5, -0.5),   # back-right  (right heel)
    3: (+0.5, +0.5),   # front-right (right toes)
}

# Rolling window durations (seconds)
ROLLING_MEAN_WINDOW = 1.0
ROLLING_STD_WINDOW  = 2.0
ROLLING_COP_WINDOW  = 0.2

# --- State machine thresholds ---

# EMPTY → ADJUSTING: aggregate must exceed B0 + STAND_THRESH_STD_FACTOR × B0_std
# and at least STAND_THRESH_MIN_PCT % of B0_agg
STAND_THRESH_STD_FACTOR = 2.0
STAND_THRESH_MIN_PCT    = 0.005     # 0.5% of B0 aggregate
PERSON_ON_HOLD_TIME     = 0.5       # seconds aggregate must stay above threshold

# ADJUSTING → STABLE STANDING: CV (std/mean) of aggregate must stay below this
STABILITY_CV_THRESH  = 0.005        # 0.5% coefficient of variation
STABILITY_HOLD_TIME  = 2.0          # seconds

# STABLE STANDING → SQUATTING: aggregate − B1 must exceed this
SQUAT_THRESH_STD_FACTOR = 2.0
SQUAT_THRESH_MIN_PCT    = 0.003     # 0.3% of B1 — actual squat peaks are only ~0.6–1.2% of B1
MIN_SQUAT_DURATION      = 0.5       # seconds — reject noise spikes

# Balance UI
TARGET_ZONE_RADIUS = 0.15           # in normalised CoP units
READY_HOLD_TIME    = 2.0            # seconds inside zone → "ready"

UI_UPDATE_MS = 33                   # ~30 fps

PROFILE_FILE = "profiles.json"
