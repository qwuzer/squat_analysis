import tkinter as tk
from queue import Queue

from config        import SERIAL_PORT, BAUD_RATE
from serial_reader import SerialReader
from rolling_stats import ChannelStats
from state_machine import SquatStateMachine
from profiles      import ProfileManager
from ui            import App


def main():
    root = tk.Tk()

    packet_queue = Queue()
    stats    = ChannelStats()
    profiles = ProfileManager()
    fsm      = SquatStateMachine(stats)

    reader = SerialReader(SERIAL_PORT, BAUD_RATE, packet_queue)
    reader.start()

    app = App(root, fsm, stats, profiles, packet_queue)
    fsm.on_rep_complete  = app.on_rep_complete
    fsm.on_state_change  = app.on_state_change

    def on_close():
        reader.stop()
        profiles.close_session()
        root.destroy()

    root.protocol('WM_DELETE_WINDOW', on_close)
    root.mainloop()


if __name__ == '__main__':
    main()
