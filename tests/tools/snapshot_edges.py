"""Regenerates the `exact` section of tests/expected_edges.yaml from the current extractor output.

Run only after reviewing the change: the snapshot is the definition of correct for the fixture.
  python tests/tools/snapshot_edges.py > /tmp/exact.txt   then diff against tests/expected_edges.yaml
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixture_graph import comparable, merged_graph  # noqa: E402


def main() -> None:
    nodes, edges = merged_graph()
    for line in comparable(edges, nodes):
        print(f"  - {line!r}" if ("'" not in line) else f'  - "{line}"')


if __name__ == "__main__":
    main()
