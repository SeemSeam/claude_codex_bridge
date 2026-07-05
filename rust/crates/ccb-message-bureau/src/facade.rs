//! Mirrors Python lib/message_bureau/facade.py
//!
//! Message Bureau facade: high-level message lifecycle management.
//!
//! Re-exports `MessageBureauFacade` from `ccb_mailbox::bureau`.

pub use ccb_mailbox::bureau::MessageBureauFacade;

// TODO: translate any additional facade.py functionality not in ccb_mailbox
