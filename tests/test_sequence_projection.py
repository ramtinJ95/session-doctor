from session_doctor.sequence_projection import (
    MAX_SEQUENCE_BINS,
    SequenceActivity,
    sequence_bins,
)


def test_sequence_bins_are_bounded_contiguous_and_reconcile_sparse_activity() -> None:
    bins = sequence_bins(
        [
            SequenceActivity("user_message", 1),
            SequenceActivity("command_failure", 500),
            SequenceActivity("file_activity", 1000),
            SequenceActivity("tool_call", None),
        ],
        1,
        1000,
    )

    assert len(bins) == MAX_SEQUENCE_BINS
    assert bins[0].first_record_index == 1
    assert bins[-1].last_record_index == 1000
    assert all(
        left.last_record_index + 1 == right.first_record_index
        for left, right in zip(bins, bins[1:], strict=False)
    )
    assert sum(sum(row.counts.model_dump().values()) for row in bins) == 3
    assert sum(row.counts.user_message for row in bins) == 1
    assert sum(row.counts.command_failure for row in bins) == 1
    assert sum(row.counts.file_activity for row in bins) == 1
    assert sum(row.counts.tool_call for row in bins) == 0
