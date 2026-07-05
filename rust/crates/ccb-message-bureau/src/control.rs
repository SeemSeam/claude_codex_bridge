//! Mirrors Python lib/message_bureau/control.py
//!
//! Control service for queue inspection and management.
//!
//! Re-exports `MessageBureauControlService` from `ccb_mailbox::bureau`.

// Re-export the control service from ccb_mailbox
pub use ccb_mailbox::bureau::MessageBureauControlService;

// TODO: translate any additional control.py functionality not in ccb_mailbox
