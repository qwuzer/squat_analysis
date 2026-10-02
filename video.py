"""
video.py — camera preview and recording alongside the mats.

A GoPro (HERO9+) on USB, with GoPro's Webcam desktop app running, shows up as
an ordinary webcam. The camera is opened when the app starts and read for as
long as it runs, so the UI always has a preview and Record starts filming at
once. While recording, it writes next to the mat data, on the mat recorder's
clock:

    <name>.mp4           the video, on a fixed FPS grid, so it plays at true speed
    <name>_frames.csv    frame, elapsed_s, capture_s

The grid works the way the mat recorder's does: each tick writes the newest
frame the camera had delivered by then, so a slow camera repeats a frame rather
than making the file play fast. `elapsed_s` is the tick (frame k is at
first_tick_s + k / FPS); `capture_s` is when that frame actually arrived, so
repeats are visible — two rows with the same capture_s are the same picture.

capture_s is when the frame reached this program, not when the light hit the
sensor. The webcam path adds a fixed delay (roughly 0.1–0.3 s) that this does
not remove. Measure it once with a stomp — a spike in the mat data and a
visible frame — and subtract it at analysis.

The camera is optional. If it cannot be opened, the mat recording carries on
and the sidecar says why there is no video.
"""

import csv
import json
import os
import re
import socket
import threading
import time
import urllib.request

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


def gopro_url():
    """The GoPro's address on its USB network link, or None.

    Over USB the camera is 172.2X.1YZ.51 and gives the computer an address on
    the same /24, so the camera's address is ours with the last octet as 51.
    """
    try:
        addrs = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return None
    for a in addrs:
        ip = a[4][0]
        if re.match(r'172\.2\d\.1\d\d\.\d+$', ip):
            return 'http://' + ip.rsplit('.', 1)[0] + '.51:8080'
    return None


def gopro_webcam_start(url, res=12, fov=0, timeout=3.0):
    """Ask the camera to start streaming as a webcam. res 12 = 1080p, 7 = 720p;
    fov 0 = wide. Returns the camera's reply, or raises on no answer."""
    q = f'{url}/gopro/webcam/start?res={res}&fov={fov}'
    return json.loads(urllib.request.urlopen(q, timeout=timeout).read() or b'{}')


class Camera:
    """One camera, read continuously: a live preview frame at all times, and
    an mp4 on a fixed grid while recording."""

    LIVE_WINDOW  = 2.0  # s; live = at least LIVE_CHANGES picture changes
    LIVE_CHANGES = 5    #    within the last LIVE_WINDOW seconds
    ASK_EVERY    = 10.0 # s between requests to a GoPro that is not streaming
    REOPEN_S    = 3.0   # s between attempts when the camera is missing

    def __init__(self, index, fps=30, size=(1280, 720), gopro=True):
        self.index  = index
        self.fps    = fps
        self.size   = size            # written size; frames are resized to it
        self.gopro  = gopro
        self.status = 'off'           # off | opening | live | not live | error: ...
        self.frame  = None            # newest picture, for the preview
        self.frame_id = 0             # bumps with every new picture
        self._lock  = threading.Lock()
        self._rec   = None            # recording state while recording
        self._closed = threading.Event()
        self._thread = None
        self.last = None              # what the last recording reported

    # ── lifetime ─────────────────────────────────────────────────────────────

    def open(self):
        """Start reading in the background. Returns at once."""
        if not HAS_CV2:
            self.status = 'error: opencv not installed'
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def close(self):
        self.stop()
        self._closed.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    # ── recording ────────────────────────────────────────────────────────────

    @property
    def recording(self):
        return self._rec is not None

    @property
    def written(self):
        r = self._rec
        return r['written'] if r else 0

    def start(self, base_path, clock):
        """Begin writing `<base_path>.mp4`. `clock()` returns the mat
        recorder's elapsed_s, so the two files share one time axis. If the
        camera is not live yet, recording begins as soon as it is."""
        if not HAS_CV2:
            return False
        with self._lock:
            self._rec = {'base': base_path, 'clock': clock, 'writer': None,
                         'log': None, 'fh': None, 't0': None, 'written': 0,
                         'captured': 0, 'repeats': 0, 'last_cap': None,
                         'error': None}
        return True

    def stop(self):
        """Stop writing and return what the sidecar should say about the video."""
        with self._lock:
            r, self._rec = self._rec, None
        if r is None:
            return None
        if r['writer'] is not None:
            r['writer'].release()
            r['fh'].close()
        if r['error']:
            status = 'error: ' + r['error']
        elif r['t0'] is None:
            status = f'error: no live picture ({self.status})'
        else:
            status = 'ok'
        self.last = {
            'file': (os.path.basename(r['base']) + '.mp4'
                     if r['t0'] is not None else None),
            'status': status,
            'fps': self.fps,
            'size': list(self.size),
            'camera_index': self.index,
            'frames_written': r['written'],
            'frames_captured': r['captured'],
            'repeated_ticks': r['repeats'],
            'first_tick_s': None if r['t0'] is None else round(r['t0'], 4),
        }
        return self.last

    # ── reader thread ────────────────────────────────────────────────────────

    def _run(self):
        while not self._closed.is_set():
            self.status = 'opening'
            cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                self.status = f'error: camera {self.index} would not open'
                self._closed.wait(self.REOPEN_S)
                continue
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            self._read(cap)
            cap.release()
            if not self._closed.is_set():
                self.status = 'error: camera lost'
                self._closed.wait(self.REOPEN_S)

    def _read(self, cap):
        """Read until the camera fails or the app closes."""
        prev, changes = None, []        # times the picture changed, last 2 s
        fails, asked_at = 0, None
        opened_at = time.monotonic()
        while not self._closed.is_set():
            ok, f = cap.read()                  # blocks until the next frame
            now = time.monotonic()
            if not ok:
                fails += 1
                if fails > 30:
                    return
                time.sleep(0.05)
                continue
            fails = 0

            # Live means the picture changes. With no stream, GoPro's virtual
            # webcam sends black or its logo card — both perfectly still; a
            # real camera never repeats a frame exactly (sensor noise). Several
            # changes are needed, because the switch from black to the logo
            # card is itself one change.
            small = cv2.resize(f, (160, 90))
            if prev is not None and cv2.absdiff(small, prev).any():
                changes.append(now)
            prev = small
            changes = [t for t in changes if now - t < self.LIVE_WINDOW]
            live = len(changes) >= self.LIVE_CHANGES
            self.status = 'live' if live else 'not live'

            # The GoPro streams only once asked. Ask after giving an already-
            # streaming camera a moment to show itself, and again every
            # ASK_EVERY seconds while there is still no live picture.
            if self.gopro and not live and now - opened_at > 3 and \
                    (asked_at is None or now - asked_at > self.ASK_EVERY):
                asked_at = now
                threading.Thread(target=self._ask_gopro, daemon=True).start()

            with self._lock:
                self.frame, self.frame_id = f, self.frame_id + 1
                if self._rec is not None:
                    self._write(self._rec, f, live)

    @staticmethod
    def _ask_gopro():
        url = gopro_url()
        if url:
            try:
                gopro_webcam_start(url)
            except Exception:
                pass

    def _write(self, r, f, live):
        """Called with each new picture while recording (under the lock)."""
        if r['error']:
            return
        now = r['clock']()
        try:
            if r['writer'] is None:
                if not live:
                    return                     # wait for a live picture
                r['writer'] = cv2.VideoWriter(
                    r['base'] + '.mp4', cv2.VideoWriter_fourcc(*'mp4v'),
                    self.fps, self.size)
                r['fh'] = open(r['base'] + '_frames.csv', 'w', newline='',
                               encoding='utf-8')
                r['log'] = csv.writer(r['fh'])
                r['log'].writerow(['frame', 'elapsed_s', 'capture_s'])
                r['t0'] = now                  # the first tick sits on this frame
            else:
                # ticks before this picture arrived get the one before it, so
                # a tick never shows a picture from after its own time
                period = 1.0 / self.fps
                img = None
                while r['t0'] + r['written'] * period < now:
                    if img is None:
                        img = r['img']
                    r['writer'].write(img)
                    r['log'].writerow([r['written'],
                                       f"{r['t0'] + r['written'] * period:.4f}",
                                       f"{r['cap_t']:.4f}"])
                    if r['cap_t'] == r['last_cap']:
                        r['repeats'] += 1
                    r['last_cap'] = r['cap_t']
                    r['written'] += 1
            r['img'] = cv2.resize(f, self.size) \
                if f.shape[1::-1] != tuple(self.size) else f
            r['cap_t'] = now
            r['captured'] += 1
        except Exception as e:                 # never take the mat recording down
            r['error'] = str(e)
