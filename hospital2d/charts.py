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
        sys.exit('No learning.csv yet: run python learn.py first (it writes a row every round).')
    with open(CSV, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit('learning.csv has no data yet: wait for the first round to end.')
    rounds = column(rows, 'round')
    fig, ax = plt.subplots(2, 3, figsize=(20, 9))
    fig.suptitle(f'How the Boss is learning ({len(rows)} rounds)')

    a = ax[0][0]
    a.fill_between(rounds, column(rows, 'worst'), column(rows, 'best'), color='lightgray', label='best / worst hospital')
    a.plot(rounds, column(rows, 'reward'), color='gray', linewidth=0.8, label='average')
    a.plot(rounds, smooth(column(rows, 'reward')), color='tab:red', label='trend (last 10)')
    a.set(title='Score of each round (higher = better)', xlabel='round', ylabel='treated - 3 x gave up - Red waiting min')
    a.legend()

    a = ax[0][1]
    for name, label in (('w_waited', 'minutes waited'), ('w_impatience', 'impatience')):
        a.plot(rounds, column(rows, name), label=label)
    a.set(title='Who goes first within a colour: what it learned', xlabel='round', ylabel='weight')
    a.legend()

    a = ax[1][0]
    for name, color in (('treated', 'tab:green'), ('gave_up', 'tab:red'), ('red_wait_min', 'tab:purple')):
        a.plot(rounds, smooth(column(rows, name)), color=color, label=name.replace('_', ' '))
    a.set(title='Per round, per hospital (trend)', xlabel='round', ylabel='patients / minutes')
    a.legend()

    a = ax[1][1]
    error = column(rows, 'error_s')
    a.plot(rounds, error, color='lightgray', label='each round')
    a.plot(rounds, smooth(error), color='tab:blue', label='trend (last 10)')
    a.set(title='How wrong its guesses of task times are', xlabel='round', ylabel='average error (s)')
    a.legend()

    a = ax[0][2]
    diff = column(rows, 'vs_start')
    a.bar(rounds, [d or 0 for d in diff], color=['tab:green' if (d or 0) > 0 else 'tab:red' for d in diff],
          label='each round')
    a.plot(rounds, smooth(diff, 20), color='black', label='trend (last 20)')
    a.axhline(0, color='gray', linewidth=0.8)
    a.set(title='Learned weights vs the original ones, same day\n(above 0 = what it learned is better)',
          xlabel='round', ylabel='score difference')
    a.legend()
    ax[1][2].axis('off')

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
