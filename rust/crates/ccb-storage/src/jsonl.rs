use camino::Utf8Path;
use serde::{de::DeserializeOwned, Serialize};
use std::fs;
use std::io::{self, BufRead, Read, Seek, Write};

const TAIL_CHUNK_SIZE: usize = 8192;
const FIND_LAST_CHUNK_SIZE: usize = 4096;

/// JSONL (JSON Lines) append-only store. Mirrors Python `storage.jsonl_store.JsonlStore`.
#[derive(Clone)]
pub struct JsonlStore;

impl JsonlStore {
    pub fn new() -> Self {
        Self
    }

    pub fn append<T: Serialize>(&self, path: &Utf8Path, row: &T) -> crate::Result<()> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut f = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)?;
        let json = serde_json::to_string(row)?;
        f.write_all(json.as_bytes())?;
        f.write_all(b"\n")?;
        Ok(())
    }

    pub fn read_all<T: DeserializeOwned>(&self, path: &Utf8Path) -> crate::Result<Vec<T>> {
        let file = fs::File::open(path)?;
        let reader = io::BufReader::new(file);
        let mut results = Vec::new();
        for line in reader.lines() {
            let line = line?;
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            let value: T = serde_json::from_str(trimmed)?;
            results.push(value);
        }
        Ok(results)
    }

    /// Read rows starting after `start_line` (0-based count of non-empty rows).
    /// Returns the updated line count and the rows.
    pub fn read_since<T: DeserializeOwned>(
        &self,
        path: &Utf8Path,
        start_line: usize,
    ) -> crate::Result<(usize, Vec<T>)> {
        let file = fs::File::open(path)?;
        let reader = io::BufReader::new(file);
        let mut results = Vec::new();
        let mut current = 0usize;
        for line in reader.lines() {
            let line = line?;
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            current += 1;
            if current <= start_line {
                continue;
            }
            let value: T = serde_json::from_str(trimmed)?;
            results.push(value);
        }
        Ok((current, results))
    }

    /// Read the last `limit` non-empty rows without loading the whole file.
    /// Mirrors Python `JsonlStore.read_tail`.
    pub fn read_tail<T: DeserializeOwned>(
        &self,
        path: &Utf8Path,
        limit: usize,
    ) -> crate::Result<Vec<T>> {
        if limit == 0 {
            return Ok(Vec::new());
        }
        let mut file = fs::File::open(path)?;
        let mut rows = Vec::new();
        let mut position = file.seek(io::SeekFrom::End(0))? as usize;
        let mut carry: Vec<u8> = Vec::new();

        while position > 0 && rows.len() < limit {
            let read_size = TAIL_CHUNK_SIZE.min(position);
            position -= read_size;
            file.seek(io::SeekFrom::Start(position as u64))?;
            let mut chunk = vec![0u8; read_size];
            file.read_exact(&mut chunk)?;

            let buffer = [chunk.as_slice(), carry.as_slice()].concat();
            let lines: Vec<&[u8]> = buffer.split(|&b| b == b'\n').collect();
            let starts_with_newline =
                !buffer.is_empty() && (buffer[0] == b'\n' || buffer[0] == b'\r');
            let keep_first = position > 0 && !buffer.is_empty() && !starts_with_newline;

            let (lines_to_process, new_carry): (Vec<&[u8]>, Vec<u8>) = if keep_first {
                let mut iter = lines.into_iter();
                let first = iter.next().unwrap_or(&[]);
                (iter.rev().collect(), first.to_vec())
            } else {
                (lines.into_iter().rev().collect(), Vec::new())
            };
            carry = new_carry;

            for raw in lines_to_process {
                if rows.len() >= limit {
                    break;
                }
                let text = String::from_utf8_lossy(raw).trim().to_string();
                if text.is_empty() {
                    continue;
                }
                let value: T = serde_json::from_str(&text)?;
                rows.push(value);
            }
        }

        if !carry.is_empty() && rows.len() < limit {
            let text = String::from_utf8_lossy(&carry).trim().to_string();
            if !text.is_empty() {
                let value: T = serde_json::from_str(&text)?;
                rows.push(value);
            }
        }

        rows.reverse();
        Ok(rows)
    }

    /// Find the last non-empty row matching `predicate` without loading the whole file.
    /// Mirrors Python `JsonlStore.find_last`.
    pub fn find_last<T: DeserializeOwned>(
        &self,
        path: &Utf8Path,
        predicate: impl Fn(&T) -> bool,
    ) -> crate::Result<Option<T>> {
        let mut file = fs::File::open(path)?;
        let mut position = file.seek(io::SeekFrom::End(0))? as usize;
        let mut carry: Vec<u8> = Vec::new();

        while position > 0 {
            let read_size = FIND_LAST_CHUNK_SIZE.min(position);
            position -= read_size;
            file.seek(io::SeekFrom::Start(position as u64))?;
            let mut chunk = vec![0u8; read_size];
            file.read_exact(&mut chunk)?;

            let buffer = [chunk.as_slice(), carry.as_slice()].concat();
            let lines: Vec<&[u8]> = buffer.split(|&b| b == b'\n').collect();
            let starts_with_newline =
                !buffer.is_empty() && (buffer[0] == b'\n' || buffer[0] == b'\r');
            let keep_first = position > 0 && !buffer.is_empty() && !starts_with_newline;

            let (lines_to_process, new_carry): (Vec<&[u8]>, Vec<u8>) = if keep_first {
                let mut iter = lines.into_iter();
                let first = iter.next().unwrap_or(&[]);
                (iter.rev().collect(), first.to_vec())
            } else {
                (lines.into_iter().rev().collect(), Vec::new())
            };
            carry = new_carry;

            for raw in lines_to_process {
                let text = String::from_utf8_lossy(raw).trim().to_string();
                if text.is_empty() {
                    continue;
                }
                let value: T = serde_json::from_str(&text)?;
                if predicate(&value) {
                    return Ok(Some(value));
                }
            }
        }

        if !carry.is_empty() {
            let text = String::from_utf8_lossy(&carry).trim().to_string();
            if !text.is_empty() {
                let value: T = serde_json::from_str(&text)?;
                if predicate(&value) {
                    return Ok(Some(value));
                }
            }
        }

        Ok(None)
    }
}

impl Default for JsonlStore {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::{Deserialize, Serialize};
    use tempfile::TempDir;

    #[derive(Debug, Serialize, Deserialize, PartialEq, Clone)]
    struct Row {
        id: i32,
        msg: String,
    }

    #[test]
    fn test_append_and_read() {
        let dir = TempDir::new().unwrap();
        let p = dir.path().join("test.jsonl");
        let path = Utf8Path::from_path(&p).unwrap();
        let store = JsonlStore::new();
        store
            .append(
                path,
                &Row {
                    id: 1,
                    msg: "a".into(),
                },
            )
            .unwrap();
        store
            .append(
                path,
                &Row {
                    id: 2,
                    msg: "b".into(),
                },
            )
            .unwrap();
        store
            .append(
                path,
                &Row {
                    id: 3,
                    msg: "c".into(),
                },
            )
            .unwrap();
        let all: Vec<Row> = store.read_all(path).unwrap();
        assert_eq!(all.len(), 3);
        assert_eq!(all[0].id, 1);
        assert_eq!(all[2].id, 3);
    }

    #[test]
    fn test_read_since() {
        let dir = TempDir::new().unwrap();
        let p = dir.path().join("since.jsonl");
        let path = Utf8Path::from_path(&p).unwrap();
        let store = JsonlStore::new();
        for i in 1..=5 {
            store
                .append(
                    path,
                    &Row {
                        id: i,
                        msg: format!("m{i}"),
                    },
                )
                .unwrap();
        }
        let (count, rows): (usize, Vec<Row>) = store.read_since(path, 2).unwrap();
        assert_eq!(count, 5);
        assert_eq!(rows.len(), 3);
        assert_eq!(rows[0].id, 3);
    }

    #[test]
    fn test_read_tail() {
        let dir = TempDir::new().unwrap();
        let p = dir.path().join("tail.jsonl");
        let path = Utf8Path::from_path(&p).unwrap();
        let store = JsonlStore::new();
        for i in 0..10 {
            store
                .append(
                    path,
                    &Row {
                        id: i,
                        msg: format!("m{i}"),
                    },
                )
                .unwrap();
        }
        let tail: Vec<Row> = store.read_tail(path, 3).unwrap();
        assert_eq!(tail.len(), 3);
        assert_eq!(tail[0].id, 7);
    }

    #[test]
    fn test_find_last() {
        let dir = TempDir::new().unwrap();
        let p = dir.path().join("find.jsonl");
        let path = Utf8Path::from_path(&p).unwrap();
        let store = JsonlStore::new();
        store
            .append(
                path,
                &Row {
                    id: 1,
                    msg: "a".into(),
                },
            )
            .unwrap();
        store
            .append(
                path,
                &Row {
                    id: 2,
                    msg: "target".into(),
                },
            )
            .unwrap();
        store
            .append(
                path,
                &Row {
                    id: 3,
                    msg: "b".into(),
                },
            )
            .unwrap();
        let found: Option<Row> = store.find_last(path, |r: &Row| r.msg == "target").unwrap();
        assert_eq!(found.unwrap().id, 2);
    }

    #[test]
    fn test_read_tail_zero_limit() {
        let dir = TempDir::new().unwrap();
        let p = dir.path().join("empty_tail.jsonl");
        let path = Utf8Path::from_path(&p).unwrap();
        let store = JsonlStore::new();
        store
            .append(
                path,
                &Row {
                    id: 1,
                    msg: "a".into(),
                },
            )
            .unwrap();
        let tail: Vec<Row> = store.read_tail(path, 0).unwrap();
        assert!(tail.is_empty());
    }
}
