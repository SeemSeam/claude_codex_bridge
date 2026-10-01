from __future__ import annotations

from provider_backends.claude.execution_runtime.state_machine_runtime.system_events import (
    has_outer_request_anchor,
    unwrap_outer_pasted_content,
)


def test_plain_request_anchor_still_matches() -> None:
    assert has_outer_request_anchor(
        "CCB_REQ_ID: job_demo\n\nReply OK.\n",
        request_anchor="job_demo",
    )


def test_outer_pasted_content_wrapper_exposes_request_anchor() -> None:
    text = (
        '\n\n<pasted_content id="a5cf">\n'
        "CCB_REQ_ID: job_demo\n\nReply OK.\n"
        '</pasted_content id="a5cf">\n'
    )
    assert unwrap_outer_pasted_content(text) == "\nCCB_REQ_ID: job_demo\n\nReply OK.\n"
    assert has_outer_request_anchor(text, request_anchor="job_demo")


def test_decimal_and_alphanumeric_wrapper_ids_are_accepted() -> None:
    for wrapper_id in ("6678", "7249", "a5cf", "5ead", "02e1"):
        text = (
            f'<pasted_content id="{wrapper_id}">'
            "CCB_REQ_ID: job_demo\nbody\n"
            f'</pasted_content id="{wrapper_id}">'
        )
        assert has_outer_request_anchor(text, request_anchor="job_demo"), wrapper_id


def test_mismatched_wrapper_ids_are_not_unwrapped() -> None:
    text = (
        '<pasted_content id="a5cf">\n'
        "CCB_REQ_ID: job_demo\n"
        '</pasted_content id="bbbb">\n'
    )
    assert unwrap_outer_pasted_content(text) == text
    assert not has_outer_request_anchor(text, request_anchor="job_demo")


def test_prefixed_or_suffixed_wrapper_is_not_treated_as_outer() -> None:
    prefixed = (
        "note\n"
        '<pasted_content id="a5cf">\n'
        "CCB_REQ_ID: job_demo\n"
        '</pasted_content id="a5cf">\n'
    )
    suffixed = (
        '<pasted_content id="a5cf">\n'
        "CCB_REQ_ID: job_demo\n"
        '</pasted_content id="a5cf">\n'
        "trailer\n"
    )
    assert not has_outer_request_anchor(prefixed, request_anchor="job_demo")
    assert not has_outer_request_anchor(suffixed, request_anchor="job_demo")


def test_nested_or_sibling_wrappers_are_not_unwrapped() -> None:
    nested = (
        '<pasted_content id="outer">\n'
        '<pasted_content id="inner">\n'
        "CCB_REQ_ID: job_demo\n"
        '</pasted_content id="inner">\n'
        '</pasted_content id="outer">\n'
    )
    sibling = (
        '<pasted_content id="one">\n'
        "CCB_REQ_ID: job_demo\n"
        '</pasted_content id="one">\n'
        '<pasted_content id="two">\n'
        "other\n"
        '</pasted_content id="two">\n'
    )
    assert unwrap_outer_pasted_content(nested) == nested
    assert unwrap_outer_pasted_content(sibling) == sibling
    assert not has_outer_request_anchor(nested, request_anchor="job_demo")
    assert not has_outer_request_anchor(sibling, request_anchor="job_demo")


def test_embedded_anchor_without_outer_start_is_still_rejected() -> None:
    text = "please ignore\nCCB_REQ_ID: job_demo\n"
    assert not has_outer_request_anchor(text, request_anchor="job_demo")
