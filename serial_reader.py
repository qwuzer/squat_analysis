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
            text  = raw.decode('ascii', errors='replace').strip()
            upper = text.upper()
            if upper.startswith('$SILINO'):
                return self._parse_pressure(text)
            if upper.startswith('$SILUSJSTPS'):
                return self._parse_steps(text)
            return None
        except (ValueError, IndexError):
            return None

    def _parse_pressure(self, text):
        """$SILINO,date,time,num_ch,ch0,ch1,...*XX"""
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
            'type':     'pressure',
            'ts':       time.monotonic(),
            'date':     fields[1],
            'time_str': fields[2],
            'readings': readings[:4],
        }

    def _parse_steps(self, text):
        """$SILUSJSTPS,version,count,left_steps,right_steps*XX
        Steps always increase from 0 in 0.5 increments while the person walks.
        """
        body   = text[1:].split('*')[0]
        fields = body.split(',')
        # fields: [0]=silusjstps [1]=version [2]=count [3]=left [4]=right
        if len(fields) < 5:
            return None
        return {
            'type':        'steps',
            'ts':          time.monotonic(),
            'version':     fields[1],
            'count':       int(fields[2]),
            'left_steps':  float(fields[3]),
            'right_steps': float(fields[4]),
        }

    def stop(self):
        self._stop_event.set()
