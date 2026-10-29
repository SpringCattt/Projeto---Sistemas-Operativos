# dialogue.py - What people say in their speech bubbles, read from data/dialogue.txt ("situation | text").
import os
from collections import defaultdict

LINES = defaultdict(list)
with open(os.path.join(os.path.dirname(__file__), 'data', 'dialogue.txt'), encoding='utf-8') as f:
    for line in f:
        if '|' in line and not line.startswith('#'):
            key, text = line.split('|', 1)
            LINES[key.strip()].append(text.strip())


def pick(rng, key, **values):
    """A random line for that situation, with {reason}, {color}, {name} filled in ('' if there is none)."""
    options = LINES.get(key)
    return rng.choice(options).format(**values) if options else ''
