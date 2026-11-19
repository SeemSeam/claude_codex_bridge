use camino::Utf8PathBuf;
use serde::{de::DeserializeOwned, Serialize};

use crate::json::JsonStore;
use crate::paths::PathLayout;

/// Stores completion cursors on disk under `layout.cursor_path(job_id)`.
/// Mirrors Python `storage.cursor_store.CursorStore`.
#[derive(Clone, Default)]
pub struct CursorStore {
    store: JsonStore,
}

impl CursorStore {
    pub fn new() -> Self {
        Self {
            store: JsonStore::new(),
        }
    }

    pub fn with_store(store: JsonStore) -> Self {
        Self { store }
    }

    /// Load the cursor for `job_id` if the file exists.
    pub fn load<C: DeserializeOwned>(
        &self,
        layout: &PathLayout,
        job_id: &str,
    ) -> crate::Result<Option<C>> {
        let path = layout.cursor_path(job_id);
        if !path.exists() {
            return Ok(None);
        }
        let value = self.store.load::<C>(&path)?;
        Ok(Some(value))
    }

    /// Save `cursor` for `job_id` and return the path written.
    pub fn save<C: Serialize>(
        &self,
        layout: &PathLayout,
        job_id: &str,
        cursor: &C,
    ) -> crate::Result<Utf8PathBuf> {
        let path = layout.cursor_path(job_id);
        self.store.save(&path, cursor)?;
        Ok(path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::{Deserialize, Serialize};

    #[derive(Debug, Serialize, Deserialize, PartialEq)]
    struct DummyCursor {
        source_kind: String,
        offset: u64,
    }

    #[test]
    fn test_cursor_store_round_trip() {
        let tmp = tempfile::TempDir::new().unwrap();
        let layout =
            PathLayout::new(camino::Utf8PathBuf::from_path_buf(tmp.path().join("repo")).unwrap());
        let store = CursorStore::new();
        let cursor = DummyCursor {
            source_kind: "test".into(),
            offset: 42,
        };

        let path = store.save(&layout, "job-1", &cursor).unwrap();
        assert!(path.exists());

        let loaded: Option<DummyCursor> = store.load(&layout, "job-1").unwrap();
        assert_eq!(loaded, Some(cursor));
    }

    #[test]
    fn test_cursor_store_missing_returns_none() {
        let tmp = tempfile::TempDir::new().unwrap();
        let layout =
            PathLayout::new(camino::Utf8PathBuf::from_path_buf(tmp.path().join("repo")).unwrap());
        let store = CursorStore::new();

        let loaded: Option<DummyCursor> = store.load(&layout, "missing-job").unwrap();
        assert_eq!(loaded, None);
    }
}
