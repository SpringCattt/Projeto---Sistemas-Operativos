# charts.py - Graphs of how the Boss is learning, from learning.csv (works while learn.py is still running):
#   python charts.py        -> writes charts.png and opens it
import csv
import os
import sys
import time

import matplotlib

matplotlib.use('Agg')  # straight to a file, no window
import matplotlib.pyplot as plt  # noqa: E402

from learn import CSV, HERE  # noqa: E402
from world import FEATURES  # noqa: E402

OUT = os.path.join(HERE, 'charts.png')


def column(rows, name):
    return [float(r[name]) if r[name] != '' else None for r in rows]


def smooth(values, n=10):
    """Moving average of the last n values (gaps skipped): the trend under the noise."""
    out = []
    for i in range(len(values)):
        window = [v for v in values[max(0, i - n + 1):i + 1] if v is not None]
        out.append(sum(window) / len(window) if window else None)
    return out


def main():
    if not os.path.exists(CSV):
        sys.exit('No learning.csv yet: run python learn.py first (it writes a row every simulated minute).')
    with open(CSV, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit('learning.csv has no data yet: wait a simulated minute.')
    minutes = column(rows, 'minute')
    fig, ax = plt.subplots(2, 2, figsize=(14, 9))
    fig.suptitle(f'How the Boss is learning ({len(rows)} simulated minutes)')

    error = column(rows, 'error_s')
    a = ax[0][0]
    a.plot(minutes, error, color='lightgray', label='each minute')
    a.plot(minutes, smooth(error), color='tab:red', label='trend (last 10)')
    a.set(title='How wrong its guesses are (lower = it learned)', xlabel='simulated minute',
          ylabel='average error (s)')
    a.legend()

    a = ax[0][1]
    for name in FEATURES:
        a.plot(minutes, column(rows, f'consult_{name}'), label=name)
    a.set(title='What it has learned about consultations (weights)', xlabel='simulated minute',
          ylabel='seconds per unit')
    a.legend()

    a = ax[1][0]
    for name, color in (('treated', 'tab:green'), ('gave_up', 'tab:red'), ('arrived', 'tab:blue')):
        a.plot(minutes, smooth(column(rows, name)), color=color, label=name.replace('_', ' '))
    a.set(title='Hospital results per minute (trend)', xlabel='simulated minute', ylabel='patients')
    a.legend()

    a = ax[1][1]
    for name, color in (('wait_green', 'tab:green'), ('wait_yellow', 'goldenrod'), ('wait_red', 'tab:red')):
        a.plot(minutes, smooth(column(rows, name)), color=color, label=name.replace('wait_', ''))
    a.set(title='Wait to see a doctor, by urgency (trend)', xlabel='simulated minute', ylabel='seconds')
    a.legend()

    fig.tight_layout()
    out = OUT
    try:
        fig.savefig(out, dpi=110)
    except OSError:  # charts.png is still open in an image viewer that locks it: a new file instead
        out = os.path.join(HERE, f'charts_{time.strftime("%H%M%S")}.png')
        fig.savefig(out, dpi=110)
    print(f'Saved {out}')
    if os.name == 'nt' and '--no-open' not in sys.argv:
        os.startfile(out)


if __name__ == '__main__':
    main()
