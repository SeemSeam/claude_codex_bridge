use camino::Utf8Path;
use serde::{de::DeserializeOwned, Serialize};
use std::fs;

use crate::atomic::{atomic_write_json, to_json_pretty_2};

/// Atomic JSON file store. Loads and saves JSON files with write-to-temp-then-rename
/// for crash safety. Mirrors Python `storage.json_store.JsonStore`.
#[derive(Clone)]
pub struct JsonStore;

impl JsonStore {
    pub fn new() -> Self {
        Self
    }

    pub fn load<T: DeserializeOwned>(&self, path: &Utf8Path) -> crate::Result<T> {
        let data = fs::read_to_string(path)?;
        let value: T = serde_json::from_str(&data)?;
        Ok(value)
    }

    pub fn save<T: Serialize>(&self, path: &Utf8Path, value: &T) -> crate::Result<()> {
        atomic_write_json(path, value)
    }

    /// Serialize value as pretty JSON (2-space indentation) without writing to disk.
    pub fn to_string<T: Serialize>(&self, value: &T) -> crate::Result<String> {
        to_json_pretty_2(value)
    }
}

impl Default for JsonStore {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::{Deserialize, Serialize};
    use tempfile::TempDir;

    #[derive(Debug, Serialize, Deserialize, PartialEq)]
    struct TestData {
        name: String,
        value: i32,
    }

    #[test]
    fn test_round_trip() {
        let dir = TempDir::new().unwrap();
        let p = dir.path().join("test.json");
        let path = Utf8Path::from_path(&p).unwrap();
        let store = JsonStore::new();
        let data = TestData {
            name: "hello".into(),
            value: 42,
        };
        store.save(path, &data).unwrap();
        let loaded: TestData = store.load(path).unwrap();
        assert_eq!(loaded, data);
    }

    #[test]
    fn test_load_missing_file() {
        let store = JsonStore::new();
        let p = std::path::PathBuf::from("/nonexistent.json");
        let result = store.load::<TestData>(Utf8Path::from_path(&p).unwrap());
        assert!(result.is_err());
    }

    #[test]
    fn test_pretty_indent_is_two_spaces() {
        let dir = TempDir::new().unwrap();
        let p = dir.path().join("pretty.json");
        let path = Utf8Path::from_path(&p).unwrap();
        let store = JsonStore::new();
        store
            .save(
                path,
                &TestData {
                    name: "x".into(),
                    value: 1,
                },
            )
            .unwrap();
        let text = fs::read_to_string(path).unwrap();
        assert!(text.contains("  \"name\": \"x\""), "content: {text}");
        assert!(text.ends_with('\n'));
    }
}
