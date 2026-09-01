pub mod atomic;
pub mod cursor_store;
pub mod json;
pub mod json_store;
pub mod jsonl;
pub mod jsonl_store;
pub mod locks;
pub mod path_helpers;
pub mod paths;
pub mod paths_agents;
pub mod paths_ccbd;
pub mod paths_targets;
pub mod project_identity;
pub mod text_artifacts;

use std::io;

#[derive(Debug, thiserror::Error)]
pub enum StorageError {
    #[error("io error: {0}")]
    Io(#[from] io::Error),
    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("not found: {0}")]
    NotFound(String),
    #[error("corrupt data: {0}")]
    Corrupt(String),
}

pub type Result<T> = std::result::Result<T, StorageError>;
