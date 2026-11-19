//! Mirrors Python `lib/storage/jsonl_store.py`.
//!
//! Provides the `JsonlStore` JSONL abstraction plus the v8.0.4 strict-tail
//! helper path gated by `CCB_RUST_JSONL_STORE`.

use camino::Utf8Path;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::env;

pub use crate::jsonl::JsonlStore;

/// Kinds of errors reported by the strict JSONL tail helpers.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum JsonlStrictErrorKind {
    NonObject,
    InvalidJson,
    InvalidUtf8,
    ReadError,
}

/// Error returned by strict JSONL tail helpers.
#[derive(Debug, Clone)]
pub struct JsonlStrictError {
    pub kind: JsonlStrictErrorKind,
    pub path: String,
    pub message: String,
}

impl std::fmt::Display for JsonlStrictError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} at {}: {}", self.kind_name(), self.path, self.message)
    }
}

impl std::error::Error for JsonlStrictError {}

impl JsonlStrictError {
    fn kind_name(&self) -> &'static str {
        match self.kind {
            JsonlStrictErrorKind::NonObject => "non_object",
            JsonlStrictErrorKind::InvalidJson => "invalid_json",
            JsonlStrictErrorKind::InvalidUtf8 => "invalid_utf8",
            JsonlStrictErrorKind::ReadError => "read_error",
        }
    }
}

/// One request in a strict tail batch.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TailRequest {
    pub id: String,
    pub path: String,
    pub n: usize,
}

/// Rows returned for one request in a strict tail batch.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TailResponse {
    pub id: String,
    pub rows: Vec<Map<String, Value>>,
}

/// Full response from a strict tail batch.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TailBatchResponse {
    pub requests: Vec<TailResponse>,
}

/// Returns true when the environment requests the strict JSONL helper path.
///
/// Mirrors Python `_strict_jsonl_helper_required()`.
pub fn strict_jsonl_helper_required() -> bool {
    match env::var("CCB_RUST_JSONL_STORE") {
        Ok(v) => matches!(
            v.trim().to_lowercase().as_str(),
            "1" | "true" | "yes" | "on" | "required"
        ),
        Err(_) => false,
    }
}

/// Strictly read the last `n` JSON object rows from `path`.
///
/// Returns an error if a row is not a JSON object or if the file cannot be
/// read/parsed. Mirrors Python `rust_helpers_jsonl.read_jsonl_tail_strict_required`.
pub fn read_jsonl_tail_strict_required(
    path: &Utf8Path,
    n: usize,
) -> Result<Vec<Map<String, Value>>, JsonlStrictError> {
    let store = JsonlStore::new();
    let rows: Vec<Value> = store.read_tail(path, n).map_err(|e| JsonlStrictError {
        kind: classify_store_error(&e),
        path: path.to_string(),
        message: e.to_string(),
    })?;

    rows.into_iter()
        .map(|value| {
            value.as_object().cloned().ok_or_else(|| JsonlStrictError {
                kind: JsonlStrictErrorKind::NonObject,
                path: path.to_string(),
                message: "expected JSON object row".into(),
            })
        })
        .collect()
}

/// Strictly read the last `n` JSON object rows for each request in the batch.
pub fn read_jsonl_tail_strict_batch_required(
    requests: &[TailRequest],
) -> Result<TailBatchResponse, JsonlStrictError> {
    let mut responses = Vec::with_capacity(requests.len());
    for request in requests {
        let path = Utf8Path::new(&request.path);
        let rows = read_jsonl_tail_strict_required(path, request.n)?;
        responses.push(TailResponse {
            id: request.id.clone(),
            rows,
        });
    }
    Ok(TailBatchResponse {
        requests: responses,
    })
}

fn classify_store_error(error: &crate::StorageError) -> JsonlStrictErrorKind {
    match error {
        crate::StorageError::Json(_) => JsonlStrictErrorKind::InvalidJson,
        crate::StorageError::Io(_) => JsonlStrictErrorKind::ReadError,
        crate::StorageError::NotFound(_) => JsonlStrictErrorKind::ReadError,
        crate::StorageError::Corrupt(_) => JsonlStrictErrorKind::InvalidJson,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn strict_helper_required_env() {
        let original = env::var("CCB_RUST_JSONL_STORE").ok();
        env::remove_var("CCB_RUST_JSONL_STORE");
        assert!(!strict_jsonl_helper_required());
        env::set_var("CCB_RUST_JSONL_STORE", "1");
        assert!(strict_jsonl_helper_required());
        env::set_var("CCB_RUST_JSONL_STORE", "required");
        assert!(strict_jsonl_helper_required());
        env::set_var("CCB_RUST_JSONL_STORE", "0");
        assert!(!strict_jsonl_helper_required());
        match original {
            Some(v) => env::set_var("CCB_RUST_JSONL_STORE", v),
            None => env::remove_var("CCB_RUST_JSONL_STORE"),
        }
    }

    #[test]
    fn strict_tail_returns_object_rows() {
        let dir = TempDir::new().unwrap();
        let file_path = dir.path().join("rows.jsonl");
        let path = Utf8Path::from_path(&file_path).unwrap();
        let store = JsonlStore::new();
        for i in 0..5 {
            let row = serde_json::json!({"id": i, "msg": format!("m{i}")});
            store.append(path, &row).unwrap();
        }
        let rows = read_jsonl_tail_strict_required(path, 2).unwrap();
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0]["id"], 3);
        assert_eq!(rows[1]["id"], 4);
    }

    #[test]
    fn strict_tail_rejects_non_object_rows() {
        let dir = TempDir::new().unwrap();
        let file_path = dir.path().join("bad.jsonl");
        let path = Utf8Path::from_path(&file_path).unwrap();
        std::fs::write(path, "\"not an object\"\n").unwrap();
        let err = read_jsonl_tail_strict_required(path, 1).unwrap_err();
        assert_eq!(err.kind, JsonlStrictErrorKind::NonObject);
    }

    #[test]
    fn strict_tail_batch() {
        let dir = TempDir::new().unwrap();
        let file_path_a = dir.path().join("a.jsonl");
        let file_path_b = dir.path().join("b.jsonl");
        let path_a = Utf8Path::from_path(&file_path_a).unwrap();
        let path_b = Utf8Path::from_path(&file_path_b).unwrap();
        let store = JsonlStore::new();
        store.append(path_a, &serde_json::json!({"id": 1})).unwrap();
        store.append(path_b, &serde_json::json!({"id": 2})).unwrap();

        let resp = read_jsonl_tail_strict_batch_required(&[
            TailRequest {
                id: "a".into(),
                path: path_a.to_string(),
                n: 1,
            },
            TailRequest {
                id: "b".into(),
                path: path_b.to_string(),
                n: 1,
            },
        ])
        .unwrap();
        assert_eq!(resp.requests.len(), 2);
        assert_eq!(resp.requests[0].rows[0]["id"], 1);
        assert_eq!(resp.requests[1].rows[0]["id"], 2);
    }
}
