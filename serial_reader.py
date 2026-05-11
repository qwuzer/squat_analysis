import threading
import time
import serial


class SerialReader(threading.Thread):
    def __init__(self, port, baud, packet_queue):
        super().__init__(daemon=True)
        self.port         = port
        self.baud         = baud
        self.queue        = packet_queue
        self._stop_event  = threading.Event()

    def run(self):
        try:
            with serial.Serial(self.port, baudrate=self.baud,
                               bytesize=8, parity='N', stopbits=1, timeout=1) as ser:
                while not self._stop_event.is_set():
                    raw = ser.readline()
                    if not raw:
                        continue
                    pkt = self._parse(raw)
                    if pkt:
                        self.queue.put(pkt)
        except serial.SerialException as exc:
            self.queue.put({'error': str(exc)})

    def _parse(self, raw):
        try:
            text = raw.decode('ascii', errors='replace').strip()
            if not text.upper().startswith('$SILINO'):
                return None
            body   = text[1:].split('*')[0]
            fields = body.split(',')
            # fields: [0]=silino [1]=date [2]=time [3]=num_ch [4..]=readings
            if len(fields) < 5:
                return None
            num_ch   = int(fields[3])
            readings = [int(fields[4 + i]) for i in range(num_ch)
                        if (4 + i) < len(fields)]
            if len(readings) < 4:
                return None
            return {
                'ts':       time.monotonic(),
                'date':     fields[1],
                'time_str': fields[2],
                'readings': readings[:4],   # always exactly 4 channels
            }
        except (ValueError, IndexError):
            return None

    def stop(self):
        self._stop_event.set()
