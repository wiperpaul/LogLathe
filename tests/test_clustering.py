from asim_forge.clustering import _parameter_slots
from asim_forge.models import SourceEvent


def test_slot_examples_keep_the_first_five_distinct_values_per_slot() -> None:
    events = [
        SourceEvent(
            source_file="test.log",
            line_number=index + 1,
            text=f"user user{index // 2} from host{max(0, index - 10)}",
        )
        for index in range(20)
    ]

    slots = _parameter_slots("user <VAR:TEXT> from <VAR:TEXT>", events)

    assert [slot.examples for slot in slots] == [
        [f"user{index}" for index in range(5)],
        [f"host{index}" for index in range(5)],
    ]
    assert [slot.occurrence for slot in slots] == [1, 2]
