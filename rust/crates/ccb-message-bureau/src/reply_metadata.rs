//! Mirrors Python lib/message_bureau/reply_metadata.py
//!
//! Reply metadata structures and operations.
//!
//! Re-exports functions from `ccb_mailbox::reply_metadata`.

pub use ccb_mailbox::reply_metadata::{
    reply_heartbeat_silence_seconds, reply_last_progress_at, reply_notice, reply_notice_kind,
};

// TODO: translate any additional reply_metadata.py functionality not in ccb_mailbox
