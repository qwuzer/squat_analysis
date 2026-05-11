import json
import os
import time
from datetime import datetime

from config import PROFILE_FILE


class ProfileManager:

    def __init__(self, path=PROFILE_FILE):
        self.path            = path
        self.profiles        = self._load()
        self.current_uid     = None
        self._session        = None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {}

    def save(self):
        with open(self.path, 'w') as f:
            json.dump(self.profiles, f, indent=2)

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def list_users(self):
        """Returns list of (uid, display_name)."""
        return [(uid, p['display_name']) for uid, p in self.profiles.items()]

    def create_user(self, display_name):
        uid = str(int(time.time() * 1000))
        self.profiles[uid] = {
            'user_id':      uid,
            'display_name': display_name,
            'sessions':     [],
        }
        self.save()
        return uid

    def select_user(self, uid):
        self.current_uid = uid
        self._session = {
            'timestamp': datetime.now().isoformat(),
            'B0':        None,
            'B1_net':    None,
            'CoP_B1':    None,
            'drift_end': None,
            'reps':      [],
        }

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    def update_baselines(self, B0, B1_net, CoP_B1):
        if self._session:
            self._session['B0']     = B0
            self._session['B1_net'] = B1_net
            self._session['CoP_B1'] = list(CoP_B1)

    def add_rep(self, rep):
        if self._session:
            self._session['reps'].append({
                'duration':       round(rep['duration'], 3),
                'peak_magnitude': round(rep['peak_magnitude'], 1),
                'cop_at_peak':    [round(v, 4) for v in rep['cop_at_peak']],
                'cop_deviation':  [round(v, 4) for v in rep['cop_deviation']],
            })

    def close_session(self, drift_end=None):
        if self._session and self.current_uid:
            self._session['drift_end'] = drift_end
            self.profiles[self.current_uid]['sessions'].append(self._session)
            self.save()
            self._session = None

    @property
    def current_display_name(self):
        if self.current_uid and self.current_uid in self.profiles:
            return self.profiles[self.current_uid]['display_name']
        return '—'
