"""
read_raw.py — minimal multi-port reader.
Reads $SILINO pressure packets from COM5, COM6, COM7 and prints them.
Run with:  python read_raw.py
Ctrl+C to stop.
"""

import threading
import serial

PORTS    = ["COM5", "COM6", "COM7"]
BAUD     = 1_500_000


def read_port(port):
    print(f"[{port}] opening...")
    try:
        with serial.Serial(port, baudrate=BAUD,
                           bytesize=8, parity='N', stopbits=1, timeout=1) as ser:
            print(f"[{port}] connected")
            while True:
                raw = ser.readline()
                if not raw:
                    continue
                text = raw.decode('ascii', errors='replace').strip()
                if not text.upper().startswith('$SILINO'):
                    continue

                # $SILINO,date,time,num_ch,ch0,ch1,...*XX
                body   = text[1:].split('*')[0]
                fields = body.split(',')
                if len(fields) < 5:
                    continue

                num_ch   = int(fields[3])
                readings = []
                for i in range(num_ch):
                    idx = 4 + i
                    if idx < len(fields):
                        readings.append(int(fields[idx]))

                print(f"[{port}]  channels={num_ch}  readings={readings}")

    except serial.SerialException as e:
        print(f"[{port}] ERROR: {e}")


threads = [threading.Thread(target=read_port, args=(p,), daemon=True) for p in PORTS]
for t in threads:
    t.start()

try:
    for t in threads:
        t.join()
except KeyboardInterrupt:
    print("\nstopped")
