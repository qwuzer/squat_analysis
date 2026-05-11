import collections


class _TimeWindow:
    """Stores (timestamp, scalar) pairs and evicts entries older than `duration`."""

    def __init__(self, duration):
        self.duration = duration
        self._buf     = collections.deque()

    def push(self, ts, value):
        self._buf.append((ts, value))
        self._evict(ts)

    def _evict(self, now):
        while self._buf and (now - self._buf[0][0]) > self.duration:
            self._buf.popleft()

    def values(self):
        return [v for _, v in self._buf]

    def mean(self):
        v = self.values()
        return sum(v) / len(v) if v else 0.0

    def std(self):
        v = self.values()
        if len(v) < 2:
            return 0.0
        m  = sum(v) / len(v)
        var = sum((x - m) ** 2 for x in v) / len(v)
        return var ** 0.5

    def __len__(self):
        return len(self._buf)


class ChannelStats:
    """Rolling statistics for all 4 channels, aggregate and CoP."""

    def __init__(self):
        from config import ROLLING_MEAN_WINDOW, ROLLING_STD_WINDOW, ROLLING_COP_WINDOW

        self._mean_w  = [_TimeWindow(ROLLING_MEAN_WINDOW) for _ in range(4)]
        self._agg_m   = _TimeWindow(ROLLING_MEAN_WINDOW)
        self._agg_s   = _TimeWindow(ROLLING_STD_WINDOW)
        self._cop_x   = _TimeWindow(ROLLING_COP_WINDOW)
        self._cop_y   = _TimeWindow(ROLLING_COP_WINDOW)
        self.last_readings = [0, 0, 0, 0]

    def push(self, ts, readings):
        self.last_readings = list(readings)
        agg = sum(readings)
        self._agg_m.push(ts, agg)
        self._agg_s.push(ts, agg)
        for i, r in enumerate(readings):
            self._mean_w[i].push(ts, r)

        # Instantaneous CoP (ratio formula — drift- and weight-invariant)
        if agg > 0:
            cx = 0.5 * (readings[3] + readings[2] - readings[0] - readings[1]) / agg
            cy = 0.5 * (readings[0] + readings[3] - readings[1] - readings[2]) / agg
        else:
            cx, cy = 0.0, 0.0
        self._cop_x.push(ts, cx)
        self._cop_y.push(ts, cy)

    @property
    def rolling_agg_mean(self):
        return self._agg_m.mean()

    @property
    def rolling_agg_std(self):
        return self._agg_s.std()

    @property
    def rolling_cop(self):
        return (self._cop_x.mean(), self._cop_y.mean())

    @property
    def channel_means(self):
        return [w.mean() for w in self._mean_w]
