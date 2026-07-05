use thiserror::Error;

#[derive(Error, Debug)]
pub enum CcbError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("not found: {0}")]
    NotFound(String),

    #[error("invalid state: {0}")]
    InvalidState(String),

    #[error("permission denied: {0}")]
    PermissionDenied(String),

    #[error("timeout: {0}")]
    Timeout(String),

    #[error("boundary blocked: {0}")]
    BoundaryBlocked(String),
}

pub type Result<T> = std::result::Result<T, CcbError>;
