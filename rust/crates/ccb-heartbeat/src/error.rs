use ccb_storage::StorageError;

/// Errors originating in `ccb-heartbeat`.
#[derive(Debug, thiserror::Error)]
pub enum HeartbeatError {
    #[error("validation error: {0}")]
    Validation(String),

    #[error("storage error: {0}")]
    Storage(#[from] StorageError),
}

/// Convenience result type for heartbeat operations.
pub type Result<T> = std::result::Result<T, HeartbeatError>;
