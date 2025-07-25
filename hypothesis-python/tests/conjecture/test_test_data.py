# This file is part of Hypothesis, which may be found at
# https://github.com/HypothesisWorks/hypothesis/
#
# Copyright the Hypothesis Authors.
# Individual contributors are listed in AUTHORS.rst and the git log.
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at https://mozilla.org/MPL/2.0/.

import itertools

import pytest

from hypothesis import strategies as st
from hypothesis.errors import Frozen, InvalidArgument
from hypothesis.internal.conjecture.data import (
    MAX_DEPTH,
    ConjectureData,
    DataObserver,
    Overrun,
    Status,
    StopTest,
    structural_coverage,
)
from hypothesis.strategies import SearchStrategy

from tests.conjecture.common import buffer_size_limit, interesting_origin


def test_cannot_draw_after_freeze():
    d = ConjectureData.for_choices((True,))
    d.draw_boolean()
    d.freeze()
    with pytest.raises(Frozen):
        d.draw_boolean()


def test_can_double_freeze():
    d = ConjectureData.for_choices([])
    d.freeze()
    assert d.frozen
    d.freeze()
    assert d.frozen


def test_draw_past_end_sets_overflow():
    d = ConjectureData.for_choices((True,))

    d.draw_boolean()
    with pytest.raises(StopTest) as e:
        d.draw_boolean()

    assert e.value.testcounter == d.testcounter
    assert d.frozen
    assert d.status == Status.OVERRUN


def test_notes_repr():
    d = ConjectureData.for_choices([])
    d.note(b"hi")
    assert repr(b"hi") in d.output


def test_can_mark_interesting():
    d = ConjectureData.for_choices([])
    with pytest.raises(StopTest):
        d.mark_interesting(interesting_origin())
    assert d.frozen
    assert d.status == Status.INTERESTING


def test_can_mark_invalid():
    d = ConjectureData.for_choices([])
    with pytest.raises(StopTest):
        d.mark_invalid()
    assert d.frozen
    assert d.status == Status.INVALID


def test_can_mark_invalid_with_why():
    d = ConjectureData.for_choices([])
    with pytest.raises(StopTest):
        d.mark_invalid("some reason")
    assert d.frozen
    assert d.status == Status.INVALID
    assert d.events == {"invalid because": "some reason"}


class BoomStrategy(SearchStrategy):
    def do_draw(self, data):
        data.draw_boolean()
        raise ValueError


def test_closes_interval_on_error_in_strategy():
    d = ConjectureData.for_choices((True,))
    with pytest.raises(ValueError):
        d.draw(BoomStrategy())
    d.freeze()
    assert not any(eg.end is None for eg in d.spans)


class BigStrategy(SearchStrategy):
    def do_draw(self, data):
        data.draw_bytes(10**6, 10**6)


def test_does_not_double_freeze_in_interval_close():
    d = ConjectureData.for_choices((b"hi",))
    with pytest.raises(StopTest):
        d.draw(BigStrategy())
    assert d.frozen
    assert not any(eg.end is None for eg in d.spans)


def test_triviality():
    d = ConjectureData.for_choices((True, False, b"1"))

    d.start_span(label=1)
    d.draw(st.booleans())
    d.draw(st.booleans())
    d.stop_span()

    d.start_span(label=2)
    d.draw_bytes(1, 1, forced=bytes([2]))
    d.stop_span()

    d.freeze()

    def trivial(u, v):
        ex = next(ex for ex in d.spans if ex.start == u and ex.end == v)
        return all(node.trivial for node in d.nodes[ex.start : ex.end])

    assert not trivial(0, 2)
    assert not trivial(0, 1)
    assert trivial(1, 2)
    assert trivial(2, 3)


def test_example_depth_marking():
    d = ConjectureData.for_choices((0,) * 6)
    d.draw(st.integers())  # v1
    d.start_span("inner")
    d.draw(st.integers())  # v2
    d.draw(st.integers())  # v3
    d.stop_span()
    d.draw(st.integers())  # v4
    d.freeze()

    assert len(d.spans) == 6

    depths = [(ex.choice_count, ex.depth) for ex in d.spans]
    assert depths == [
        (4, 0),  # top
        (1, 1),  # v1
        (2, 1),  # inner
        (1, 2),  # v2
        (1, 2),  # v3
        (1, 1),  # v4
    ]


def test_has_examples_even_when_empty():
    d = ConjectureData.for_choices([])
    d.draw(st.just(False))
    d.freeze()
    assert d.spans


def test_has_cached_examples_even_when_overrun():
    d = ConjectureData.for_choices((False,))
    d.start_span(3)
    d.draw_boolean()
    d.stop_span()
    try:
        d.draw_boolean()
    except StopTest:
        pass
    assert d.status == Status.OVERRUN
    assert any(ex.label == 3 and ex.choice_count == 1 for ex in d.spans)
    assert d.spans is d.spans


def test_can_observe_draws():
    class LoggingObserver(DataObserver):
        def __init__(self):
            self.log = []

        def draw_boolean(self, value: bool, *, was_forced: bool, constraints: dict):
            self.log.append(("draw_boolean", value, was_forced))

        def draw_integer(self, value: int, *, was_forced: bool, constraints: dict):
            self.log.append(("draw_integer", value, was_forced))

        def conclude_test(self, *args):
            assert d.frozen
            self.log.append(("concluded", *args))

    observer = LoggingObserver()
    d = ConjectureData.for_choices((True, 1, 3), observer=observer)

    origin = interesting_origin()
    d.draw_boolean()
    d.draw_integer(0, 2**7 - 1, forced=10)
    d.draw_integer(0, 2**8 - 1)
    with pytest.raises(StopTest):
        d.conclude_test(Status.INTERESTING, interesting_origin=origin)

    assert observer.log == [
        ("draw_boolean", True, False),
        ("draw_integer", 10, True),
        ("draw_integer", 3, False),
        ("concluded", Status.INTERESTING, origin),
    ]


def test_calls_concluded_implicitly():
    class NoteConcluded(DataObserver):
        def conclude_test(self, status, reason):
            assert d.frozen
            self.conclusion = (status, reason)

    observer = NoteConcluded()

    d = ConjectureData.for_choices((True,), observer=observer)
    d.draw_boolean()
    d.freeze()

    assert observer.conclusion == (Status.VALID, None)


def test_examples_show_up_as_discarded():
    d = ConjectureData.for_choices((True, False, True))

    d.start_span(1)
    d.draw_boolean()
    d.stop_span(discard=True)
    d.start_span(1)
    d.draw_boolean()
    d.stop_span()
    d.freeze()

    assert len([ex for ex in d.spans if ex.discarded]) == 1


def test_examples_support_negative_indexing():
    d = ConjectureData.for_choices((True, True))
    d.draw(st.booleans())
    d.draw(st.booleans())
    d.freeze()
    assert d.spans[-1].choice_count == 1


def test_examples_out_of_bounds_index():
    d = ConjectureData.for_choices((False,))
    d.draw(st.booleans())
    d.freeze()
    with pytest.raises(IndexError):
        d.spans[10]


def test_can_override_label():
    d = ConjectureData.for_choices((False,))
    d.draw(st.booleans(), label=7)
    d.freeze()
    assert any(ex.label == 7 for ex in d.spans)


def test_will_mark_too_deep_examples_as_invalid():
    d = ConjectureData.for_choices((0,))

    s = st.integers()
    for _ in range(MAX_DEPTH + 1):
        s = s.map(lambda x: None)

    with pytest.raises(StopTest):
        d.draw(s)
    assert d.status == Status.INVALID


def test_empty_strategy_is_invalid():
    d = ConjectureData.for_choices([])
    with pytest.raises(StopTest):
        d.draw(st.nothing())
    assert d.status == Status.INVALID


def test_will_error_on_find():
    d = ConjectureData.for_choices([])
    d.is_find = True
    with pytest.raises(InvalidArgument):
        d.draw(st.data())


def test_can_note_non_str():
    d = ConjectureData.for_choices([])
    x = object()
    d.note(x)
    assert repr(x) in d.output


def test_can_note_str_as_non_repr():
    d = ConjectureData.for_choices([])
    d.note("foo")
    assert d.output == "foo"


def test_result_is_overrun():
    d = ConjectureData.for_choices([])
    with pytest.raises(StopTest):
        d.draw_boolean()
    assert d.as_result() is Overrun


def test_trivial_before_force_agrees_with_trivial_after():
    d = ConjectureData.for_choices((False, True, True))
    d.draw_boolean()
    d.draw_boolean(forced=True)
    d.draw_boolean()

    t1 = [d.nodes[i].trivial for i in range(3)]
    d.freeze()
    r = d.as_result()
    t2 = [n.trivial for n in r.nodes]
    t3 = [r.nodes[i].trivial for i in range(3)]

    assert t1 == t2 == t3


def test_events_are_noted():
    d = ConjectureData.for_choices([])
    d.events["hello"] = ""
    assert "hello" in d.events


def test_child_indices():
    d = ConjectureData.for_choices((True,) * 4)

    d.start_span(0)  # examples[1]
    d.start_span(1)  # examples[2]
    d.draw(st.booleans())  # examples[3] (st.booleans)
    d.draw(st.booleans())  # examples[4] (st.booleans)
    d.stop_span()
    d.stop_span()
    d.draw(st.booleans())  # examples[5] (st.booleans)
    d.draw(st.booleans())  # examples[6] (st.booleans)
    d.freeze()

    assert list(d.spans.children[0]) == [1, 5, 6]
    assert list(d.spans.children[1]) == [2]
    assert list(d.spans.children[2]) == [3, 4]

    assert d.spans[0].parent is None
    for ex in list(d.spans)[1:]:
        assert ex in d.spans[ex.parent].children


def test_example_equality():
    d = ConjectureData.for_choices((False, False))

    d.start_span(0)
    d.draw_boolean()
    d.stop_span()
    d.start_span(0)
    d.draw_boolean()
    d.stop_span()
    d.freeze()

    examples = list(d.spans)
    for ex1, ex2 in itertools.combinations(examples, 2):
        assert ex1 != ex2
        assert not (ex1 == ex2)  # noqa

    for ex in examples:
        assert ex == ex
        assert not (ex != ex)  # noqa

        assert not (ex == "hello")  # noqa
        assert ex != "hello"


def test_structural_coverage_is_cached():
    assert structural_coverage(50) is structural_coverage(50)


def test_examples_create_structural_coverage():
    data = ConjectureData.for_choices([])
    data.start_span(42)
    data.stop_span()
    data.freeze()
    assert structural_coverage(42) in data.tags


def test_discarded_examples_do_not_create_structural_coverage():
    data = ConjectureData.for_choices([])
    data.start_span(42)
    data.stop_span(discard=True)
    data.freeze()
    assert structural_coverage(42) not in data.tags


def test_children_of_discarded_examples_do_not_create_structural_coverage():
    data = ConjectureData.for_choices([])
    data.start_span(10)
    data.start_span(42)
    data.stop_span()
    data.stop_span(discard=True)
    data.freeze()
    assert structural_coverage(42) not in data.tags
    assert structural_coverage(10) not in data.tags


def test_overruns_at_exactly_max_length():
    with buffer_size_limit(1):
        data = ConjectureData(prefix=[True], random=None, max_choices=1)
        data.draw_boolean()
        try:
            data.draw_boolean()
        except StopTest:
            pass
        assert data.status is Status.OVERRUN
