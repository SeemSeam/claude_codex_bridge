//! Mirrors Python lib/message_bureau/store.py
//!
//! Storage abstractions for message bureau data.
//!
//! Re-exports from `ccb_mailbox::stores`.

pub use ccb_mailbox::stores::{AttemptStore, MessageStore, ReplyStore};

// TODO: translate any additional store.py functionality not in ccb_mailbox
