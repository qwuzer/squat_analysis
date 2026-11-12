"""
protocol.py — the collection script, and which bands each pose should load.

The order, targets and read-out instructions follow the subject guide
(瑜伽壓力墊實驗：受試者動作指引): six poses, the one-sided ones done right side
first; each pose-side 3 correct holds of 30 s, then error A and error B once,
20 s each. mat_ui walks through STEPS so the operator never picks a label.

Mats, front to back along the row: Mat 1 front, Mat 2 middle, Mat 3 back.
Each has four bands (TL, TR, BL, BR); T is the band nearer the front of the
row, L/R as seen facing the front. A band is named `<mat>.<band>`, e.g. `2.TR`.

No tkinter or serial here, so recordings can be replayed through it offline.
"""

MATS = ('front', 'middle', 'back')            # Mat 1, Mat 2, Mat 3
BANDS = ('TL', 'TR', 'BL', 'BR')

CORRECT_S = 30
ERROR_S = 20
CORRECT_REPS = 3


def _bands(spec):
    """'1:TL TR 3:TL' -> ('1.TL', '1.TR', '3.TL')"""
    out, mat = [], None
    for tok in spec.split():
        if ':' in tok:
            mat, tok = tok.split(':')
        out.append(f'{mat}.{tok}')
    return tuple(out)


ALL = _bands('1:TL TR BL BR 2:TL TR BL BR 3:TL TR BL BR')


def band_name(b):
    """'2.TR' -> 'middle mat right-front'"""
    mat, band = b.split('.')
    side = 'left' if band[1] == 'L' else 'right'
    end = 'front' if band[0] == 'T' else 'back'
    return f'{MATS[int(mat) - 1]} mat {side}-{end}'


class Footprint:
    """Bands a pose must load (any one of `need`), and which may also be loaded.
    Every band in neither is expected to be empty."""

    def __init__(self, *need, may=''):
        self.need = [_bands(n) for n in need]
        self.may = _bands(may) if may else ()

    def check(self, loaded):
        """loaded: set of band names over the threshold.
        Returns (ok, missing, extra) for the alternative that fits best."""
        best = None
        for need in self.need:
            allowed = set(need) | set(self.may) | {b for n in self.need for b in n}
            missing = [b for b in need if b not in loaded]
            extra = sorted(b for b in loaded if b not in allowed)
            score = len(missing) + len(extra)
            if best is None or score < best[0]:
                best = (score, missing, extra)
        _, missing, extra = best
        return not missing and not extra, missing, extra

    def bands(self):
        """Every band the pose may load: what the map outlines."""
        return {b for n in self.need for b in n} | set(self.may)


EMPTY_FOOTPRINT = Footprint('')


# pose, label, footprint, the correct-hold read-out, errors {code: (what, say, footprint or None)}
# Read-outs are the guide's own words. An error's footprint is None when the
# load moves within the same bands; lunge B is the one that adds a band.
POSES = [
    ('mountain', 'Mountain', Footprint('2:TL TR BL BR'),
     '雙腳與髋同寬，左腳踩左排、右腳踩右排。腳尖朝前，腳掌壓到前、後兩條。'
     '身體直立，雙手自然垂在兩側，眼睛看前方，重量平均在雙腳。',
     {'A': ('weight on heels', '把重量往後放到腳跟，腳趾放輕。', None),
      'B': ('weight on toes', '把重量往前放到腳趾，腳跟放輕。', None)}),
    ('tree_R', 'Tree R', Footprint('2:TR BR'),
     '從山式開始，把重量移到右腳。左腳掌貼在右小腿或大腿內側，避開膝蓋。'
     '雙手胸前合十。抬起的腳不要碰墊子。',
     {'A': ('weight on toes', '把重量壓到站立腳的腳掌前端。', None),
      'B': ('weight on heel', '把重量往後坐到站立腳的腳跟。', None)}),
    ('tree_L', 'Tree L', Footprint('2:TL BL'),
     '從山式開始，把重量移到左腳。右腳掌貼在左小腿或大腿內側，避開膝蓋。'
     '雙手胸前合十。抬起的腳不要碰墊子。',
     {'A': ('weight on toes', '把重量壓到站立腳的腳掌前端。', None),
      'B': ('weight on heel', '把重量往後坐到站立腳的腳跟。', None)}),
    ('lunge_R', 'Lunge R', Footprint('1:TR BR 3:TL'),
     '右腳往前踩在前墊，腳掌完全著地。左腳往後踩在後墊，只用腳掌前端著地，'
     '後腳跟抬起。前膝彎曲在腳踝正上方，後腿伸直，上身直立，雙手向上舉。',
     {'A': ('weight in back leg', '把重量往後移到後腿。', None),
      'B': ('back heel down', '把後腳跟放下，踩到墊子上。',
            Footprint('1:TR BR 3:TL BL'))}),
    ('lunge_L', 'Lunge L', Footprint('1:TL BL 3:TR'),
     '左腳往前踩在前墊，腳掌完全著地。右腳往後踩在後墊，只用腳掌前端著地，'
     '後腳跟抬起。前膝彎曲在腳踝正上方，後腿伸直，上身直立，雙手向上舉。',
     {'A': ('weight in back leg', '把重量往後移到後腿。', None),
      'B': ('back heel down', '把後腳跟放下，踩到墊子上。',
            Footprint('1:TL BL 3:TR BR'))}),
    # hands on the front mat's first or second row, by height; heels off
    ('dog', 'Downward dog', Footprint('1:TL TR 3:TL TR', '1:BL BR 3:TL TR'),
     '雙手與肩同寬，依身高放在前墊第 1 或第 2 排，手指張開。雙腳與髋同寬踩在後墊。'
     '臀部往上推成倒 V 字形，頭在兩臂之間。腳跟不要碰墊子。',
     {'A': ('too much weight in hands', '把肩膀往前移到手腕正上方。', None),
      'B': ('uneven hands', '用{side}手多壓一點。', None)}),
    # forearms load both front-mat bands; toes on the back mat's front band
    ('plank', 'Forearm plank', Footprint('1:TL TR BL BR 3:TL TR'),
     '前臂放在前墊：手肘在肩膀正下方，兩前臂平行，手肘壓後條、手壓前條。'
     '雙腳往後伸直，腳尖踩在後墊。頭、背、臀、腳跟成一直線，腹部出力。',
     {'A': ('hips piked', '把臀部稍微往上抬。', None),
      'B': ('hips sagging (mild only)', '讓臀部稍微往下沉；腰部一有不適就停止。', None)}),
    # shoulders on the middle mat's back band — to be confirmed in the pilot
    ('bridge', 'Bridge', Footprint('1:TL TR BL BR 2:BL BR'),
     '仰躺，頭朝後墊，肩膀放在中墊。屈膝，雙腳與髋同寬踩在前墊，壓到前、後兩條。'
     '雙手輕放兩側，不要壓墊子。腳跟往下踩，臀部抬起，肩膀到膝蓋成一直線。',
     {'A': ('weight on one foot', '把重量移到{side}腳。', None),
      'B': ('weight on toes', '把重量移到腳尖，腳跟放輕。', None)}),
]

SIDES = {'R': ('右', 'right'), 'L': ('左', 'left')}


class Step:
    """One hold in the script."""

    def __init__(self, pose, label, cond, rep, target, what, say, footprint):
        self.pose, self.label, self.cond, self.rep = pose, label, cond, rep
        self.target, self.what, self._say, self.footprint = target, what, say, footprint
        self.sided = '{side}' in say       # staff pick the side (dog B, bridge A)

    @property
    def key(self):
        """Stable id for progress tracking: tree_R_correct#2, tree_R_A#1."""
        return f'{self.pose}_{self.cond}#{self.rep}'

    def name(self, side='R'):
        """What the holds file records: tree_R_correct, tree_R_A, dog_B_L."""
        base = f'{self.pose}_{self.cond}'
        return f'{base}_{side}' if self.sided else base

    def say(self, side='R'):
        return self._say.format(side=SIDES[side][0])

    def title(self, side='R'):
        if self.cond == 'correct':
            return f'{self.label} · correct · {self.rep} of {CORRECT_REPS}'
        what = self.what + (f' ({SIDES[side][1]})' if self.sided else '')
        return f'{self.label} · error {self.cond}: {what}'


def build_steps():
    steps = []
    for pose, label, fp, say, errors in POSES:
        for rep in range(1, CORRECT_REPS + 1):
            steps.append(Step(pose, label, 'correct', rep, CORRECT_S, '', say, fp))
        for cond, (what, err_say, err_fp) in errors.items():
            steps.append(Step(pose, label, cond, 1, ERROR_S, what, err_say,
                              err_fp or fp))
    return steps


STEPS = build_steps()


# ── telling loaded bands from empty ones, live ────────────────────────────────
# Measured on four subjects (changjian, huang1, elijio, yj): against the empty
# hold just before a pose, a band under a foot sits +470…+1,300 counts and one
# with nothing on it −110…+120. 250 sits in that gap with room both sides.
LOAD_ON = 250
# Empty hold: a band is clean once it is back within CLEAN of the last empty
# reading and steady. On some days (elijio, yj) the first step-off leaves every
# loaded band +400…+800 high for the rest of the session; a band that has
# dropped at least DROP below its level in the pose and is steady is that
# offset, not a foot, and becomes the new empty reading. (A foot left on a
# band does not drop: weight moves onto it, if anything.) elijio's smallest
# such drop was 360, under 40% of that band's load in the pose.
CLEAN = 250
DROP = 300
DROP_FRAC = 0.3      # ...and at least this share of the band's load in the pose
STEADY = 60          # counts: change between the last two 1-s means
CLEAN_S = 7          # seconds of clean mat an empty hold needs
# Outside a timed pose the empty reading is also renewed whenever every band
# has been within CLEAN of it and steady for WATCH_S, so drift and the small
# leftover after a step-off never pile up. Mat 2 drifted ~300 counts in 25 min
# and keeps +150…+270 for 10 s after a step-off (test_20261110). Only the
# plain clean test: telling leftover from a foot needs the subject told to
# step off, which is the empty hold.
WATCH_S = 3
# A band's first step-off after a pose loaded it takes whatever steady level it
# settles to as its empty reading, however high. On test_20261112 Mat 2 kept
# +400…+700 per band after a stand, 70–90% of its load: no drop rule can tell
# that from a foot. Where it happens it happens once, at the first step-off,
# and then holds (8.10), so the later empty holds keep the normal check.


class Tracker:
    """Live empty reading, net load per band, and the empty-hold check.

    Fed one sample of every band per tick (a dict band -> raw count) with its
    time. Pure arithmetic, so recordings can be replayed through it.

    An empty hold is `ready` once the mat has been clean for CLEAN_S; the last
    CLEAN_S of that stretch becomes the reference the next pose is measured
    against. Ready latches: the subject stepping on afterwards is expected.
    Outside a timed pose the reference is also renewed after WATCH_S of a
    plainly clean mat, before Record included."""

    def __init__(self):
        self.ref = None          # band -> empty reading the map is measured against
        self._hist = []          # (t, sample) over the last ~2.5 s
        self._pose_secs = []     # 1-s means during the current pose
        self._pose_level = {}    # band -> median of those, once the pose ends
        self._hold_loaded = {}   # band -> 1-s means while loaded, this empty hold
        self._hold_next = 0.0
        self._next_sec = None
        self._clean = []         # (t, sample) of the current clean stretch
        self.in_empty = False
        self.ready = False
        self.ready_window = None  # (t0, t1) of the stretch the reference came from
        self.state = {}          # band -> 'clean' | 'offset' | 'loaded' | 'settling'
        self.zeroed_at = None    # when the reference was last renewed
        self.stepped_off = set()  # bands whose first step-off has been taken

    # ── feeding ──────────────────────────────────────────────────────────────

    def push(self, t, sample):
        self._hist.append((t, sample))
        while self._hist and t - self._hist[0][0] > 2.5:
            self._hist.pop(0)
        if self.ref is None and len(self._hist) >= 5:
            self.zero()
        if self.in_empty and not self.ready:
            self._empty_tick(t, sample)
        elif self._next_sec is not None:           # a timed pose
            if t >= self._next_sec:
                self._pose_secs.append(self._mean(t - 1, t + 1))
                self._next_sec = t + 1
        else:
            self._watch_tick(t, sample)

    def _mean(self, t0, t1):
        rows = [s for t, s in self._hist if t0 <= t < t1]
        if not rows:
            return None
        return {b: sum(r[b] for r in rows) / len(rows) for b in rows[0]}

    def level(self):
        """Mean of the last 0.3 s — what the map shows."""
        if not self._hist:
            return {}
        t = self._hist[-1][0]
        return self._mean(t - 0.3, t + 1) or {}

    def net(self):
        if self.ref is None:
            return {}
        return {b: v - self.ref[b] for b, v in self.level().items()}

    def loaded(self):
        return {b for b, v in self.net().items() if v >= LOAD_ON}

    # ── holds ────────────────────────────────────────────────────────────────

    def zero(self):
        """Take the current level as empty (used at start-up, and on Z)."""
        self.ref = dict(self.level())
        self.zeroed_at = self._hist[-1][0] if self._hist else None

    def start_empty(self):
        """A pose just ended (or Record was pressed): watch the mat empty.
        The pose's level is the median of its 1-s means, so a subject who
        stepped off before Space does not drag it down."""
        secs = [m for m in self._pose_secs if m]
        self._pose_level = {} if not secs else {
            b: sorted(m[b] for m in secs)[len(secs) // 2] for b in secs[0]}
        self._pose_secs, self._next_sec = [], None
        self.in_empty, self.ready = True, False
        self.ready_window = None
        self._clean = []
        self._hold_loaded, self._hold_next = {}, 0.0
        self.state = {}

    def start_pose(self):
        """The empty hold is over; the pose is measured against its reference
        (or the previous one, if the operator started before it was ready)."""
        self.in_empty = False
        self._pose_secs, self._next_sec = [], -1.0
        self._clean = []
        self.state = {}

    def clean_s(self, t):
        if self.ready:
            return float(CLEAN_S)
        return 0.0 if not self._clean else t - self._clean[0][0]

    def _loaded_level(self, b):
        """What the band read under load: the pose's, or — for the empty hold
        after Record, or someone stepping on mid-hold — the median of this
        hold's loaded seconds."""
        if b in self._pose_level:
            return self._pose_level[b]
        secs = sorted(self._hold_loaded.get(b, ()))
        return secs[len(secs) // 2] if len(secs) >= 2 else None

    def _dropped(self, b, v):
        level = self._loaded_level(b)
        if level is None:
            return False
        return level - v >= max(DROP, DROP_FRAC * (level - self.ref[b]))

    def _first_off(self, b, v):
        """The pose loaded this band, it has never been stepped off before,
        and it now reads below its level in the pose: what it keeps is the
        mat, not a foot."""
        level = self._pose_level.get(b)
        return (b not in self.stepped_off and level is not None
                and level - self.ref[b] >= LOAD_ON and v <= level - STEADY)

    def _empty_tick(self, t, sample):
        now = self._mean(t - 1, t + 1)
        before = self._mean(t - 2, t - 1)
        if now is None or before is None or self.ref is None:
            return
        second = t >= self._hold_next
        if second:
            self._hold_next = t + 1
        clean = True
        for b, v in now.items():
            steady = abs(v - before[b]) <= STEADY
            if v - self.ref[b] < CLEAN and steady:
                st = 'clean'
            elif steady and (self._dropped(b, v) or self._first_off(b, v)):
                st = 'offset'          # the mat kept part of the load
            elif v - self.ref[b] >= LOAD_ON:
                st = 'loaded' if steady else 'settling'
            else:
                st = 'settling'
            self.state[b] = st
            if second and st == 'loaded':      # never the leftover itself, or
                self._hold_loaded.setdefault(b, []).append(v)  # it drags this down
            clean = clean and st in ('clean', 'offset')
        if not clean:
            self._clean = []
            return
        self._clean.append((t, sample))
        if t - self._clean[0][0] >= CLEAN_S:
            self.ready = True
            self.stepped_off |= {b for b, v in self._pose_level.items()
                                 if v - self.ref[b] >= LOAD_ON}
            self._take_ref(t)

    def _watch_tick(self, t, sample):
        now = self._mean(t - 1, t + 1)
        before = self._mean(t - 2, t - 1)
        if now is None or before is None or self.ref is None:
            return
        if not all(v - self.ref[b] < CLEAN and abs(v - before[b]) <= STEADY
                   for b, v in now.items()):
            self._clean = []
            return
        self._clean.append((t, sample))
        if t - self._clean[0][0] >= WATCH_S:
            self._take_ref(t)

    def _take_ref(self, t):
        """The clean stretch becomes the empty reading. In an empty hold it is
        also the window logged to the holds file, so the newest one wins."""
        n = len(self._clean)
        self.ref = {b: sum(s[b] for _, s in self._clean) / n
                    for b in self._clean[0][1]}
        if self.in_empty:
            self.ready_window = (self._clean[0][0], t)
        self.zeroed_at = t
        self._clean = []
