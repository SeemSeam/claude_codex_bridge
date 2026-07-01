//! CCB job store abstraction.
//!
//! Canonical home for job records, submission records, job events, and the
//! persistent JSONL stores that back them. Mirrors `lib/jobs/` from Python
//! v7.5.2.

pub mod models;
pub mod store;

use thiserror::Error;

#[derive(Error, Debug)]
pub enum JobsError {
    #[error("storage error: {0}")]
    Storage(#[from] ccb_storage::StorageError),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("not found: {0}")]
    NotFound(String),
}

pub type Result<T> = std::result::Result<T, JobsError>;

// Re-export the most commonly used types at crate root for ergonomics.
pub use models::{
    DeliveryScope, JobEvent, JobRecord, JobStatus, MessageEnvelope, ProjectViewJobSummary,
    SubmissionRecord, TargetKind,
};
pub use store::{JobEventStore, JobStore, SubmissionStore};
