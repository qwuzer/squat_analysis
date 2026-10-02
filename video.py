"""
video.py — camera recording alongside the mats.

A GoPro (HERO9+) on USB, with GoPro's Webcam desktop app running, shows up as
an ordinary webcam. This records it next to the mat data, on the mat
recorder's clock:

    <name>.mp4           the video, on a fixed FPS grid, so it plays at true speed
    <name>_frames.csv    frame, elapsed_s, capture_s

The grid works the way the mat recorder's does: each tick writes the newest
frame the camera has delivered, so a slow camera repeats a frame rather than
making the file play fast. `elapsed_s` is the tick (frame k is at k / FPS);
`capture_s` is when that frame actually arrived, so repeats are visible —
two rows with the same capture_s are the same picture.

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


class VideoRecorder:
    """Records one camera to mp4 on a fixed grid, timed by an external clock."""

    def __init__(self, index, fps=30, size=(1280, 720), gopro=True):
        self.index  = index
        self.fps    = fps
        self.size   = size           # written size; frames are resized to it
        self.gopro  = gopro
        self.reset()

    def reset(self):
        self.path     = None
        self.status   = 'off'         # off | starting | recording | ok | error: ...
        self.written  = 0             # frames written (grid ticks)
        self.captured = 0             # distinct frames from the camera
        self.repeats  = 0             # ticks that reused the previous frame
        self._thread  = None
        self._stop    = threading.Event()

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, base_path, clock):
        """Begin recording to `<base_path>.mp4`. `clock()` returns the mat
        recorder's elapsed_s, so the two files share one time axis."""
        if not HAS_CV2:
            self.status = 'error: opencv not installed'
            return False
        self.reset()
        self.path = base_path + '.mp4'
        self._base = base_path
        self._clock = clock
        self.status = 'starting'
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        """Stop and return what the sidecar should say about the video."""
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=5)
        if self.status == 'off':
            return None
        return {
            'file': None if self.path is None else os.path.basename(self.path),
            'status': self.status,
            'fps': self.fps,
            'size': list(self.size),
            'camera_index': self.index,
            'frames_written': self.written,
            'frames_captured': self.captured,
            'repeated_ticks': self.repeats,
            'first_tick_s': getattr(self, '_t0', None),
        }

    # ── capture thread ───────────────────────────────────────────────────────

    def _run(self):
        if self.gopro:
            # The virtual webcam delivers black until the camera is streaming.
            # Best effort — a camera that is already streaming, or a plain
            # webcam, needs nothing.
            url = gopro_url()
            if url:
                try:
                    gopro_webcam_start(url)
                except Exception:
                    pass
        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            self.status = f'error: camera {self.index} would not open'
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)

        # Wait for a live picture. With no stream, GoPro's virtual webcam sends
        # black or its logo card — both perfectly still. A real camera never
        # gives two identical frames (sensor noise), so "changed" means "live".
        deadline = time.monotonic() + 10
        frame, prev = None, None
        while not self._stop.is_set() and time.monotonic() < deadline:
            ok, f = cap.read()
            if not ok:
                continue
            small = cv2.resize(f, (160, 90))
            if prev is not None and cv2.absdiff(small, prev).any():
                frame = f
                break
            prev = small
        if frame is None:
            cap.release()
            self.status = ('off' if self._stop.is_set()
                           else 'error: no live picture (check the GoPro Webcam app)')
            return

        writer = cv2.VideoWriter(self.path, cv2.VideoWriter_fourcc(*'mp4v'),
                                 self.fps, self.size)
        fh = open(self._base + '_frames.csv', 'w', newline='', encoding='utf-8')
        log = csv.writer(fh)
        log.writerow(['frame', 'elapsed_s', 'capture_s'])

        period = 1.0 / self.fps
        cap_t = self._clock()
        t0 = cap_t                      # the first tick sits on the first frame
        self._t0 = round(t0, 4)
        self.captured = 1
        self.status = 'recording'
        last_written_cap = None
        try:
            while not self._stop.is_set():
                ok, f = cap.read()      # blocks until the next frame
                now = self._clock()
                # Ticks before this frame arrived get the frame before it, so
                # a tick never shows a picture from after its own time.
                img = None
                while t0 + self.written * period < now:
                    if img is None:
                        img = cv2.resize(frame, self.size) \
                            if frame.shape[1::-1] != self.size else frame
                    writer.write(img)
                    log.writerow([self.written,
                                  f'{t0 + self.written * period:.4f}',
                                  f'{cap_t:.4f}'])
                    if cap_t == last_written_cap:
                        self.repeats += 1
                    last_written_cap = cap_t
                    self.written += 1
                if ok:
                    frame, cap_t = f, now
                    self.captured += 1
                elif self._stop.wait(0.01):
                    break
        except Exception as e:          # never take the mat recording down
            self.status = f'error: {e}'
        finally:
            if self.status == 'recording':
                self.status = 'ok'      # stopped cleanly
            cap.release()
            writer.release()
            fh.close()
