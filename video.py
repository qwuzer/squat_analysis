"""
video.py — camera recording alongside the mats.

The GoPro (HERO9+, on USB) is read directly: asked over its USB network link to
start webcam streaming, it sends an MPEG-TS stream to UDP port 8554 on this
computer, which OpenCV decodes. GoPro's own Webcam desktop app is not used —
it sat on a black screen or its logo card while the camera was streaming, and
it would hold port 8554, so **quit it before running the mat app**. Any other
webcam can be used by its index instead.

The camera is opened when the app starts and read for as long as it runs, so
Record starts filming at once (opening the GoPro stream takes ~7 s). While
recording, it writes next to the mat data, on the mat recorder's clock:

    <name>.mp4           the video, on a fixed FPS grid, so it plays at true speed
    <name>_frames.csv    frame, elapsed_s, capture_s

The grid works the way the mat recorder's does: each tick writes the newest
frame the camera had delivered by then, so a slow camera repeats a frame rather
than making the file play fast. `elapsed_s` is the tick (frame k is at
first_tick_s + k / FPS); `capture_s` is when that frame actually arrived, so
repeats are visible — two rows with the same capture_s are the same picture.

capture_s is when the frame reached this program, not when the light hit the
sensor. The stream adds a fixed delay that this does not remove. Measure it once
with a stomp — a spike in the mat data and a visible frame — and subtract it at
analysis.

**The camera runs in its own process** (CameraProcess). In the same process its
work starved the serial readers — Python runs one thread at a time — and the
mats delivered 0–2 frames/s instead of 100, silently, for three sessions. Its
own process, at below-normal priority, cannot take anything from the mats.
Both processes read the same system clock (time.perf_counter is system-wide on
Windows), so frames are still stamped on the mat recorder's elapsed_s.

The camera is optional. If it cannot be opened, the mat recording carries on
and the sidecar says why there is no video.
"""

import csv
import json
import multiprocessing as mp
import os
import queue
import re
import socket
import threading
import time
import urllib.request

# FFmpeg options for the GoPro stream: hand frames over as they arrive rather
# than buffering. Must be set before cv2 is imported.
os.environ.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS',
                      'fflags;nobuffer|flags;low_delay|threads;2')

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


GOPRO = 'gopro'          # pass as the camera source to read the GoPro directly
GOPRO_PORT = 8554
# timeout is in microseconds: a stalled stream ends the read so it can restart
GOPRO_STREAM = (f'udp://@0.0.0.0:{GOPRO_PORT}'
                '?overrun_nonfatal=1&fifo_size=50000000&timeout=5000000')


def port_free(port):
    """True if nothing else (e.g. GoPro's Webcam app) holds this UDP port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(('0.0.0.0', port))
        return True
    except OSError:
        return False
    finally:
        s.close()


GOPRO_FOV_LINEAR = 4   # webcam lens: 0 wide (fisheye), 2 narrow, 3 superview,
                       # 4 linear — straight lines stay straight, so posture
                       # angles measured from the video are not bent by the lens


def gopro_webcam_start(url, res=7, fov=GOPRO_FOV_LINEAR, timeout=3.0):
    """Ask the camera to (re)start streaming as a webcam. res 7 = 720p,
    12 = 1080p; fov as above. Raises if the camera does not answer.

    720p by default: the video is saved at 720p anyway, and decoding a 1080p
    stream takes enough CPU to make the UI stutter. A stream already running
    keeps its old resolution, so it is stopped first.
    """
    def get(path):
        return json.loads(urllib.request.urlopen(url + path, timeout=timeout)
                          .read() or b'{}')
    get('/gopro/webcam/stop')
    return get(f'/gopro/webcam/start?res={res}&fov={fov}')


class Camera:
    """One camera, read continuously from app start; an mp4 on a fixed grid
    while recording."""

    LIVE_WINDOW  = 2.0  # s; live = at least LIVE_CHANGES picture changes
    LIVE_CHANGES = 5    #    within the last LIVE_WINDOW seconds
    REOPEN_S     = 3.0  # s between attempts when the camera is missing

    def __init__(self, source=GOPRO, fps=30, size=(1280, 720)):
        self.source = source          # GOPRO, or a webcam index
        self.fps    = fps
        self.size   = size            # written size; frames are resized to it
        self.status = 'off'           # off | opening | live | not live | error: ...
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

    def start(self, base_path, clock):  # noqa: D401 — see CameraProcess.start
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
            'camera': self.source,
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
            cap = self._open_gopro() if self.source == GOPRO \
                else self._open_webcam()
            if cap is None:
                self._closed.wait(self.REOPEN_S)
                continue
            self._read(cap)
            cap.release()
            if not self._closed.is_set():
                self.status = 'error: camera lost'
                self._closed.wait(self.REOPEN_S)

    def _open_webcam(self):
        cap = cv2.VideoCapture(self.source, cv2.CAP_DSHOW)
        if not cap.isOpened():
            self.status = f'error: camera {self.source} would not open'
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        return cap

    def _open_gopro(self):
        url = gopro_url()
        if url is None:
            self.status = 'error: no GoPro on USB'
            return None
        if not port_free(GOPRO_PORT):
            self.status = 'error: port 8554 busy, quit GoPro Webcam app'
            return None
        try:
            gopro_webcam_start(url)
        except Exception:
            self.status = 'error: GoPro not answering'
            return None
        cap = cv2.VideoCapture(GOPRO_STREAM, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            self.status = 'error: no stream from GoPro'
            return None
        return cap

    def _read(self, cap):
        """Read until the camera fails or the app closes."""
        prev, changes = None, []        # times the picture changed, last 2 s
        fails = 0
        while not self._closed.is_set():
            ok, f = cap.read()                  # blocks until the next frame
            now = time.monotonic()
            if not ok:
                # a GoPro read only fails after its 5 s stream timeout, so
                # a few failures in a row means the stream is gone: reopen
                fails += 1
                if fails > 3:
                    return
                time.sleep(0.05)
                continue
            fails = 0

            # Live means the picture changes. A stalled source repeats one
            # picture exactly; a real camera never does (sensor noise).
            # Several changes are needed so a single cut does not count.
            small = cv2.resize(f, (160, 90))
            if prev is not None and cv2.absdiff(small, prev).any():
                changes.append(now)
            prev = small
            changes = [t for t in changes if now - t < self.LIVE_WINDOW]
            live = len(changes) >= self.LIVE_CHANGES
            self.status = 'live' if live else 'not live'

            with self._lock:
                if self._rec is not None:
                    self._write(self._rec, f, live)

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


# ── the camera in its own process ─────────────────────────────────────────────

BELOW_NORMAL_PRIORITY_CLASS = 0x4000


def _serve(cmds, events, source, fps, size):
    """Body of the camera process: run a Camera, obey commands, report state."""
    try:                                   # the mats and the UI come first
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    cam = Camera(source, fps, size)
    cam.open()
    sent = 0.0
    while True:
        try:
            cmd = cmds.get(timeout=0.25)
        except queue.Empty:
            cmd = None
        if cmd is not None:
            if cmd[0] == 'start':
                perf0 = cmd[2]
                cam.start(cmd[1], lambda: time.perf_counter() - perf0)
            elif cmd[0] == 'stop':
                events.put(('stopped', cam.stop()))
            elif cmd[0] == 'close':
                cam.close()
                return
        if time.monotonic() - sent > 0.5 or cmd is not None:
            events.put(('state', cam.status, cam.recording, cam.written))
            sent = time.monotonic()


class CameraProcess:
    """Camera's interface, but the camera runs in a child process.

    start() takes the mat recorder's perf_counter origin rather than a clock
    function: a function cannot cross into another process, a number can.
    """

    def __init__(self, source=GOPRO, fps=30, size=(1280, 720)):
        self.source, self.fps, self.size = source, fps, tuple(size)
        self.status, self.recording, self.written = 'off', False, 0
        self.last = None
        self._proc = None
        self._stopped = threading.Event()
        self._stop_meta = None

    def open(self):
        if not HAS_CV2:
            self.status = 'error: opencv not installed'
            return
        ctx = mp.get_context('spawn')
        self._cmds, self._events = ctx.Queue(), ctx.Queue()
        self._proc = ctx.Process(target=_serve, daemon=True, name='camera',
                                 args=(self._cmds, self._events, self.source,
                                       self.fps, self.size))
        self._proc.start()
        self.status = 'opening'
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        while True:
            try:
                ev = self._events.get(timeout=1.0)
            except queue.Empty:
                if self._proc is None or not self._proc.is_alive():
                    if self.status != 'off':
                        self.status = 'error: camera process ended'
                    return
                continue
            except (EOFError, OSError):
                return
            if ev[0] == 'state':
                self.status, self.recording, self.written = ev[1:]
            elif ev[0] == 'stopped':
                self._stop_meta = ev[1]
                self._stopped.set()

    def start(self, base_path, perf0):
        """Begin writing `<base_path>.mp4`, timed as perf_counter() - perf0."""
        if self._proc is None or not self._proc.is_alive():
            return False
        self._cmds.put(('start', base_path, perf0))
        self.recording, self.written = True, 0
        return True

    def stop(self):
        """Stop writing; return what the sidecar should say about the video."""
        if self._proc is None or not self._proc.is_alive():
            self.recording = False
            return None
        self._stopped.clear()
        self._cmds.put(('stop',))
        if not self._stopped.wait(5.0):
            self.last = {'status': 'error: camera did not answer stop'}
        else:
            self.last = self._stop_meta
        self.recording = False
        return self.last

    def close(self):
        if self._proc is None:
            return
        if self.recording:
            self.stop()
        self.status = 'off'
        try:
            self._cmds.put(('close',))
            self._proc.join(timeout=3)
        finally:
            if self._proc.is_alive():
                self._proc.terminate()
