pub mod bureau;
pub mod control_queue;
pub mod control_trace;
pub mod facade_recording;
pub mod facade_state;
pub mod jobs;
pub mod kernel;
pub mod models;
pub mod reply_metadata;
pub mod reply_payloads;
pub mod stores;
pub mod targets;

// Re-export the public surface that Python `lib/mailbox_kernel/__init__.py`
// exposes at package root, so the Rust crate has the same ergonomic boundary.
pub use crate::kernel::MailboxKernelService;
pub use crate::models::{
    DeliveryLease, InboundEventRecord, InboundEventStatus, InboundEventType, LeaseState,
    MailboxRecord, MailboxState, SCHEMA_VERSION,
};
pub use crate::stores::{DeliveryLeaseStore, InboundEventStore, MailboxStore};

pub mod claiming;
pub mod leasing;
pub mod mailbox;
pub mod model_codecs;
pub mod model_enums;
pub mod queries;
pub mod record_codec;
pub mod service;
pub mod service_state;
pub mod summary;
pub mod terminal;
pub mod transitions;

#[cfg(test)]
mod re_export_tests {
    // Compile-only test: every item in Python `lib/mailbox_kernel/__init__.py`
    // must be reachable from the crate root.
    use super::*;

    #[test]
    fn mailbox_kernel_public_items_re_exported() {
        let _: Option<DeliveryLease> = None;
        let _: Option<DeliveryLeaseStore> = None;
        let _: Option<InboundEventRecord> = None;
        let _: Option<InboundEventStatus> = None;
        let _: Option<InboundEventStore> = None;
        let _: Option<InboundEventType> = None;
        let _: Option<LeaseState> = None;
        let _: Option<MailboxKernelService> = None;
        let _: Option<MailboxRecord> = None;
        let _: Option<MailboxState> = None;
        let _: Option<MailboxStore> = None;
        let _ = SCHEMA_VERSION;
    }
}

use thiserror::Error;

#[derive(Error, Debug)]
pub enum MailboxError {
    #[error("storage error: {0}")]
    Storage(#[from] ccb_storage::StorageError),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("not found: {0}")]
    NotFound(String),
    #[error("record codec error: {0}")]
    RecordCodec(String),
}

pub type Result<T> = std::result::Result<T, MailboxError>;
