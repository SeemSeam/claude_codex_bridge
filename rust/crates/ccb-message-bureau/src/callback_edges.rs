//! Mirrors Python lib/message_bureau/callback_edges.py
//!
//! Callback edge tracking for parent-child job relationships.
//!
//! Re-exports from `ccb_mailbox::models` and `ccb_mailbox::stores`.

// Re-export the types and stores from ccb_mailbox
pub use ccb_mailbox::models::{CallbackEdgeRecord, CallbackEdgeState};
pub use ccb_mailbox::stores::CallbackEdgeStore;

// TODO: translate any additional callback_edges.py functionality not in ccb_mailbox
