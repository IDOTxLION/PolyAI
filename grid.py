"""The grid of a drawing in plan view: where its lines are, what a place is called after
them, and how a place on a shop drawing maps onto the plan.

A plan view carries the building's grid lines, each ending in a bubble with its label: letters
one way, numbers the other. An element drawn in plan (a footing, the bars over a column) is
named after the two lines nearest to it ("C-12"), on the plan and on the shop drawing alike.

plan_columns.find_grid reads the grid of a plan sheet from the outermost rows of short labels.
A shop drawing holds many other short labels (quantities, marks), so here a row of bubbles is
told by its order instead: its labels follow one another like the grid lines do (A, B, C...
or 1, 2, 3...), which a row of quantities does not.
"""

from bisect import bisect_left

from plan_columns import GRID_LABEL, label_order

SAME_ROW = 15  # points: bubbles this close across are in the same row
SHORTEST = 3  # a row of bubbles holds at least this many labels in order


def ordered(labels):
    """The longest run of (position, text) labels whose texts follow the order of grid lines,
    reading the positions one way or the other. Labels read wrong fall out of it."""
    best = []
    for lettered in (True, False):
        kind = sorted((position, text) for position, text in labels if text[0].isalpha() == lettered)
        for way in (kind, kind[::-1]):
            keys = [label_order(text) for _, text in way]
            runs = []  # runs[i]: the longest run ending with label i
            for i, key in enumerate(keys):
                before = max((runs[j] for j in range(i) if keys[j] < key), key=len, default=[])
                runs.append(before + [way[i]])
            best = max([best] + runs, key=len)
    return sorted(best)


def find_grid(lines):
    """Return ({x: label} of the vertical grid lines, {y: label} of the horizontal ones) from
    the (box, text) lines of a page. Either is empty when no row of bubbles is found.

    The vertical lines are named by a row of bubbles, the horizontal ones by a column of
    bubbles of the other kind (letters against numbers).
    """
    labels = [((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2, text.strip()) for box, text in lines
              if GRID_LABEL.fullmatch(text.strip())]
    found = []
    for across, along in ((1, 0), (0, 1)):  # rows of bubbles share a y; columns of bubbles share an x
        groups = []
        for label in sorted(labels, key=lambda label: label[across]):
            if groups and label[across] - groups[-1][-1][across] <= SAME_ROW:
                groups[-1].append(label)
            else:
                groups.append([label])
        runs = [ordered([(label[along], label[2]) for label in group]) for group in groups]
        found.append(sorted((run for run in runs if len(run) >= SHORTEST), key=len, reverse=True))
    rows, columns = found
    for row in rows[:1]:
        lettered = row[0][1][0].isalpha()
        column = next((column for column in columns if column[0][1][0].isalpha() != lettered), None)
        if column:
            return dict(row), dict(column)
    return {}, {}


def crossing(x, y, vertical, horizontal):
    """Name the grid crossing nearest to a point, letter first: "C-12". None without a grid."""
    if not vertical or not horizontal:
        return None
    names = [vertical[min(vertical, key=lambda p: abs(p - x))], horizontal[min(horizontal, key=lambda p: abs(p - y))]]
    names.sort(key=lambda name: not name[0].isalpha())
    return "-".join(names)


def mapping(source, target):
    """Return a function taking a position along one axis of a drawing to the same place on
    another drawing of the same grid, from the lines both name. None when they share fewer
    than two lines. Between two shared lines the position is interpolated; beyond the last
    one it follows the nearest pair.
    """
    target_of = {label: position for position, label in target.items()}
    shared = sorted((position, target_of[label]) for position, label in source.items() if label in target_of)
    if len(shared) < 2:
        return None
    here = [position for position, _ in shared]

    def convert(position):
        i = min(max(bisect_left(here, position), 1), len(shared) - 1)
        (a, b), (c, d) = shared[i - 1], shared[i]
        return b + (position - a) * (d - b) / (c - a)

    return convert
