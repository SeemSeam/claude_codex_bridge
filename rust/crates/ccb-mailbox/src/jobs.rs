//! CCB job / submission / event stores.
//!
//! The canonical implementation now lives in the `ccb-jobs` crate. This module
//! re-exports it so that existing `ccb-mailbox` callers keep compiling.

pub use ccb_jobs::store::{JobEventStore, JobStore, SubmissionStore};
