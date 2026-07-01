//! Mirrors Python lib/message_bureau/facade_recording.py
//!
//! Message Bureau facade recording operations.
//!
//! Re-exports functions from `ccb_mailbox::facade_recording`.

pub use ccb_mailbox::facade_recording::{
    claimable_request_job_ids, mark_attempt_started, record_attempt_terminal, record_notice,
    record_reply, record_retry_attempt, record_submission, record_terminal, CompletionDecision,
};

// TODO: translate any additional facade_recording.py functionality not in ccb_mailbox
