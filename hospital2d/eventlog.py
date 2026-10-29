# eventlog.py - The log process: writes every event the simulation reports to hospital.log, and the summary when
# the simulation ends. It only ever reads from its Queue.
import os

LOG_PATH = os.path.join(os.path.dirname(__file__), 'hospital.log')


def run_log(inbox, path=LOG_PATH):
    """Process body. inbox: Queue <- simulation: ('event', (time, text)), ('summary', text), or None to finish."""
    with open(path, 'w', encoding='utf-8') as f:
        while True:
            item = inbox.get()
            if item is None:
                break
            kind, payload = item
            if kind == 'event':
                t, text = payload
                f.write(f'[{int(t // 60):02d}:{t % 60:04.1f}] {text}\n')
            else:
                f.write(f'\n===== Summary =====\n{payload}\n')
            f.flush()
