#!/usr/bin/env python3
"""Build the seed git repository for the Agensh RAG run (PEPtalk).

Global context: the Agensh software-project pack imports an upstream git
repository as the team's `main` (`[workspace] seed = "git"`). This script
creates that upstream as a local repository holding the corpus: a fixed set of
Python Enhancement Proposals from https://github.com/python/peps at a pinned
commit, plus a stub README, `requirements.txt` and a corpus test so `main`
passes the CI gate from the first commit.

License: every PEP carries its own "Copyright" section; PEPs are "placed in the
public domain or under the CC0-1.0-Universal license" (see PEP 1's Copyright
section). The script includes a PEP only if its Copyright section says
"public domain" or "CC0", and records that line in corpus/SOURCE.md.

Usage: python3 make_seed.py /path/to/new/seed-repo
"""

import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

COMMIT = 'e7c5b5410b03df7883dcc7497a118e3753304643'  # python/peps main on 2026-11-18
RAW = f'https://raw.githubusercontent.com/python/peps/{COMMIT}/peps/pep-{{:04d}}.rst'
# Well-known PEPs: process/style, language features, typing, packaging and core runtime.
PEPS = [1, 8, 20, 257, 318, 343, 380, 435, 448, 465, 484, 492, 498, 506, 519, 525, 526, 530, 544,
        557, 563, 567, 570, 572, 578, 585, 586, 589, 591, 593, 597, 602, 604, 612, 613, 615, 616,
        617, 618, 634, 635, 636, 646, 647, 649, 654, 655, 657, 668, 673, 675, 678, 680, 681, 684,
        688, 692, 695, 696, 698, 701, 702, 703, 709, 723, 3107, 3115, 3119, 3120, 3129, 3132,
        3135, 3156, 3333]

TEST = '''"""The corpus is present and every file is a PEP with a title (seed check; keep passing)."""
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[1] / "corpus" / "peps"


def test_corpus_has_peps():
    files = sorted(CORPUS.glob("pep-*.rst"))
    assert len(files) >= 50
    for path in files:
        head = path.read_text(encoding="utf-8")[:2000]
        assert "PEP:" in head and "Title:" in head, path.name
'''

README = '''# PEPtalk

Retrieval-augmented question answering over Python Enhancement Proposals (PEPs).
To be built: see the project goal (issue #1).

The corpus lives in `corpus/peps/` (one reStructuredText file per PEP); its source,
pinned commit and per-file license lines are in `corpus/SOURCE.md`.
'''


def copyright_line(text):
    # The Copyright section is the last "Copyright\n=========" heading; take its first non-empty paragraph.
    match = re.search(r'^Copyright\n=+\n+(.+?)(?:\n\n|\Z)', text, re.M | re.S)
    return ' '.join(match.group(1).split()) if match else ''


def main(out):
    out = Path(out)
    peps = out / 'corpus' / 'peps'
    peps.mkdir(parents=True, exist_ok=False)  # refuse to overwrite an existing seed
    rows, skipped = [], []
    for number in PEPS:
        try:
            with urllib.request.urlopen(RAW.format(number), timeout=30) as response:
                text = response.read().decode('utf-8')
        except urllib.error.HTTPError as error:
            skipped.append(f'PEP {number}: HTTP {error.code}')
            continue
        line = copyright_line(text)
        if not re.search(r'public domain|cc0', line, re.I):
            skipped.append(f'PEP {number}: copyright section not public domain/CC0 ({line[:80]!r})')
            continue
        title = re.search(r'^Title:\s*(.+)$', text, re.M).group(1).strip()
        (peps / f'pep-{number:04d}.rst').write_text(text, encoding='utf-8')
        rows.append(f'| {number} | {title} | {line} |')
    source = [
        '# Corpus source', '',
        f'Files copied unchanged from https://github.com/python/peps at commit `{COMMIT}` (`peps/pep-NNNN.rst`).',
        'Each file was included only if its own Copyright section places it in the public domain or under CC0.', '',
        f'{len(rows)} PEPs:', '', '| PEP | Title | Copyright section |', '|---|---|---|', *rows, '',
    ]
    if skipped:
        source += ['Not included:', '', *[f'- {s}' for s in skipped], '']
    (out / 'corpus' / 'SOURCE.md').write_text('\n'.join(source), encoding='utf-8')
    (out / 'tests').mkdir()
    (out / 'tests' / 'test_corpus.py').write_text(TEST, encoding='utf-8')
    (out / 'README.md').write_text(README, encoding='utf-8')
    (out / 'requirements.txt').write_text('pytest\n', encoding='utf-8')
    git = ['git', '-C', str(out), '-c', 'user.name=Agensh seed', '-c', 'user.email=seed@localhost']
    subprocess.run([*git, 'init', '-q', '-b', 'main'], check=True)
    subprocess.run([*git, 'add', '-A'], check=True)
    subprocess.run([*git, 'commit', '-q', '-m', f'Seed: {len(rows)} PEPs from python/peps@{COMMIT[:7]}'], check=True)
    print(f'seeded {out}: {len(rows)} PEPs, {len(skipped)} skipped')
    for s in skipped:
        print('  skipped', s)


if __name__ == '__main__':
    main(sys.argv[1])
