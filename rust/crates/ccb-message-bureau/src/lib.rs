//! CCB message bureau facade and control/trace queues.
//!
//! Mirrors the corresponding Python v7.5.2 package. The canonical
//! implementation lives in `ccb_mailbox::bureau`; this crate re-exports it as
//! the dedicated message-bureau crate boundary.

// Core facade and control services
pub use ccb_mailbox::bureau::{MessageBureauControlService, MessageBureauFacade};

// Re-export the model/store types that Python `lib/message_bureau/__init__.py`
// exposes at package root, matching the Python public boundary exactly.
pub use ccb_mailbox::models::{
    AttemptRecord, AttemptState, CallbackEdgeRecord, CallbackEdgeState, MessageRecord,
    MessageState, ReplyRecord, ReplyTerminalStatus, SCHEMA_VERSION,
};
pub use ccb_mailbox::stores::{AttemptStore, CallbackEdgeStore, MessageStore, ReplyStore};

// Declare all modules to mirror Python structure
pub mod callback_edges;
pub mod control;
pub mod control_queue;
pub mod control_trace;
pub mod facade;
pub mod facade_recording;
pub mod facade_recording_common;
pub mod facade_recording_submission;
pub mod facade_recording_terminal;
pub mod facade_recording_terminal_attempts;
pub mod facade_recording_terminal_replies;
pub mod facade_state;
pub mod model_codecs;
pub mod model_enums;
pub mod models;
pub mod reply_metadata;
pub mod reply_payloads;
pub mod service_state;
pub mod store;

// Runtime submodules
pub mod control_queue_runtime;
pub mod control_trace_runtime;

/// Crate version.
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crate_smoke() {
        assert_eq!(version(), env!("CARGO_PKG_VERSION"));
    }

    #[test]
    fn facade_type_is_re_exported() {
        // Demonstrate that the message bureau facade is reachable through this
        // crate without constructing any tmux resources.
        let _: Option<MessageBureauFacade> = None;
    }

    #[test]
    fn all_message_bureau_public_items_are_re_exported() {
        // Compile-only check that every item in Python `__all__` is reachable.
        let _: Option<AttemptRecord> = None;
        let _: Option<AttemptState> = None;
        let _: Option<AttemptStore> = None;
        let _: Option<CallbackEdgeRecord> = None;
        let _: Option<CallbackEdgeState> = None;
        let _: Option<CallbackEdgeStore> = None;
        let _: Option<MessageBureauControlService> = None;
        let _: Option<MessageBureauFacade> = None;
        let _: Option<MessageRecord> = None;
        let _: Option<MessageState> = None;
        let _: Option<MessageStore> = None;
        let _: Option<ReplyRecord> = None;
        let _: Option<ReplyStore> = None;
        let _: Option<ReplyTerminalStatus> = None;
        let _ = SCHEMA_VERSION;
    }
}
