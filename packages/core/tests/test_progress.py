"""Progress: the ticks of loops that run other loops are read inside their part, so a job's progress goes through every
simulation once instead of reaching its end with the first one."""

from optlab.core import progress


class Fractions:
    def __init__(self):
        self.seen = []

    def tick(self, done, total, unit):
        self.seen.append(done / total)


def _simulation(batches):
    for j in range(batches):
        progress.tick(j + 1, batches, "batches")


def test_a_simulation_inside_a_part_ends_at_that_part_s_end_not_at_the_whole_one():
    r = Fractions()
    with progress.reporting(r):
        for i in range(4):  # four shocks, each a simulation of five batches
            with progress.part(i, 4):
                _simulation(5)
    assert r.seen == sorted(r.seen)  # never backwards
    assert max(x for x in r.seen[:5]) == 0.25  # the first simulation done: a quarter, not the whole run
    assert r.seen[-1] == 1.0


def test_parts_nest_and_do_nothing_without_a_reporter():
    r = Fractions()
    with progress.reporting(r):
        for g in range(2):  # two groups of three simulations
            with progress.part(g, 2):
                for i in range(3):
                    with progress.part(i, 3):
                        _simulation(2)
    assert r.seen == sorted(r.seen) and r.seen[-1] == 1.0
    assert abs(r.seen[1] - 1 / 6) < 1e-5  # the first simulation of the first group: a sixth (to the parts' resolution)
    with progress.part(0, 3):  # no reporter: nothing happens
        _simulation(2)
