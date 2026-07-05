//! Mirrors Python lib/message_bureau/control_queue.py
//!
//! Control queue operations for agent mailbox inspection and management.
//!
//! Re-exports functions from `ccb_mailbox::control_queue`.

pub use ccb_mailbox::control_queue::{ack_reply, agent_queue, inbox, mailbox_head, queue_summary};

// TODO: translate any additional control_queue.py functionality not in ccb_mailbox
