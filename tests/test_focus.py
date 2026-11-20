"""Focus scan for very long streams: transcribe only where chat/voice spiked, spread over the stream.
Run: python -m tests.test_focus"""

import numpy as np

from analyzer.signals import focus_windows


def main():
    n = 10 * 3600
    rng = np.random.default_rng(1)
    loud = rng.normal(0, 2, n)
    chat = list(rng.poisson(20, n // 5))
    spikes = (600, 4000, 9000, 20000, 33000)
    for t in spikes:
        loud[t:t + 20] += 15
        chat[t // 5:t // 5 + 6] = [200] * 6
    w = focus_windows(loud, chat, 0, n, budget_s=5400)
    total = sum(b - a for a, b in w)
    assert 5000 <= total <= 5400, total                                  # stays inside the budget
    assert all(any(a <= t <= b for a, b in w) for t in spikes), w        # every spike is heard
    assert all(any(a <= t - 60 for a, b in w if a <= t <= b) for t in spikes)   # build-up before the peak
    per_hour = {}
    for a, b in w:
        per_hour[a // 3600] = per_hour.get(a // 3600, 0) + b - a
    assert max(per_hour.values()) <= 0.3 * 5400 + 180, per_hour          # no single hour hogs the budget
    assert w == sorted(w) and all(b <= c for (_, b), (c, _) in zip(w, w[1:]))   # sorted, no overlap
    # offset section + no chat at all still works
    w2 = focus_windows(loud[:7200], None, 3600, 7200, budget_s=900)
    assert w2 and all(3600 <= a < b <= 7200 for a, b in w2), w2
    assert focus_windows([], None, 0, 0) == []
    # title band: an emoji never sits alone on its own line (and wrapping never crashes)
    from editor import titleband as tb
    f, e = tb._font("arialbd.ttf", 46), tb._font("seguiemj.ttf", 42)
    for t in ("Escaping with stolen cash in GTA 6 ✈", "Speed finds tigers in GTA 6 🐯", "short 😭",
              "Speed thinks a Coca-Cola is money in GTA 6 🥤"):
        ls = tb._wrap(t, f, e, 1080 * 0.78)
        assert ls and not tb.EMOJI.fullmatch(ls[-1].replace(" ", "")), ls
    assert tb.render("Airport reveal in GTA 6 🤯", 1080).shape[2] == 4
    print("test_focus: all checks passed")


if __name__ == "__main__":
    main()
