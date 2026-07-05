//! Record format compatibility layer.
//!
//! Python readers expect every persisted record to carry `schema_version` and
//! `record_type` headers.  The helpers below wrap Rust structs on write and
//! unwrap / validate them on read, merging the headers into the top-level JSON
//! object so the on-disk format matches the Python `to_record()` output exactly.

use serde::{de::DeserializeOwned, Serialize};
use serde_json::{Map, Value};

pub const SCHEMA_VERSION: u32 = 1;

fn mailbox_codec_error(msg: impl Into<String>) -> crate::MailboxError {
    crate::MailboxError::RecordCodec(msg.into())
}

fn wrap(record_type: &str, payload: Value) -> Value {
    let mut object = match payload {
        Value::Object(m) => m,
        other => {
            let mut m = Map::new();
            m.insert("payload".to_string(), other);
            m
        }
    };
    object.insert(
        "schema_version".to_string(),
        Value::Number(SCHEMA_VERSION.into()),
    );
    object.insert(
        "record_type".to_string(),
        Value::String(record_type.to_string()),
    );
    Value::Object(object)
}

fn unwrap(record_type: &str, mut value: Value) -> crate::Result<Value> {
    let object = value
        .as_object_mut()
        .ok_or_else(|| mailbox_codec_error("record payload is not a JSON object"))?;

    let version = object
        .get("schema_version")
        .and_then(|v| v.as_u64())
        .ok_or_else(|| mailbox_codec_error("missing schema_version"))?;
    if version != SCHEMA_VERSION as u64 {
        return Err(mailbox_codec_error(format!(
            "schema_version must be {SCHEMA_VERSION}, got {version}"
        )));
    }

    let actual_type = object
        .get("record_type")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if actual_type != record_type {
        return Err(mailbox_codec_error(format!(
            "record_type must be {record_type:?}, got {actual_type:?}"
        )));
    }

    object.remove("schema_version");
    object.remove("record_type");
    Ok(Value::Object(object.clone()))
}

pub fn to_value<T: Serialize>(record_type: &str, record: &T) -> crate::Result<Value> {
    let payload = serde_json::to_value(record)?;
    Ok(wrap(record_type, payload))
}

pub fn from_value<T: DeserializeOwned>(record_type: &str, value: Value) -> crate::Result<T> {
    let payload = unwrap(record_type, value)?;
    Ok(serde_json::from_value(payload)?)
}

pub fn load<T: DeserializeOwned>(
    _json: &ccb_storage::json::JsonStore,
    path: &camino::Utf8Path,
    record_type: &str,
) -> crate::Result<Option<T>> {
    if !path.exists() {
        return Ok(None);
    }
    let value: Value = serde_json::from_str(&std::fs::read_to_string(path)?)?;
    from_value(record_type, value).map(Some)
}

pub fn save<T: Serialize>(
    _json: &ccb_storage::json::JsonStore,
    path: &camino::Utf8Path,
    record_type: &str,
    record: &T,
) -> crate::Result<()> {
    let value = to_value(record_type, record)?;
    std::fs::create_dir_all(path.parent().unwrap_or(path.as_ref()))?;
    ccb_storage::atomic::atomic_write_json(path, &value).map_err(Into::into)
}

pub fn append<T: Serialize>(
    _jsonl: &ccb_storage::jsonl::JsonlStore,
    path: &camino::Utf8Path,
    record_type: &str,
    record: &T,
) -> crate::Result<()> {
    let value = to_value(record_type, record)?;
    let json = serde_json::to_string(&value)?;
    std::fs::create_dir_all(path.parent().unwrap_or(path.as_ref()))?;
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;
    std::io::Write::write_all(&mut file, json.as_bytes())?;
    std::io::Write::write_all(&mut file, b"\n")?;
    Ok(())
}

pub fn read_all<T: DeserializeOwned>(
    _jsonl: &ccb_storage::jsonl::JsonlStore,
    path: &camino::Utf8Path,
    record_type: &str,
) -> crate::Result<Vec<T>> {
    let file = std::fs::File::open(path)?;
    let reader = std::io::BufReader::new(file);
    let mut results = Vec::new();
    for line in std::io::BufRead::lines(reader) {
        let line = line?;
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let value: Value = serde_json::from_str(trimmed)?;
        results.push(from_value(record_type, value)?);
    }
    Ok(results)
}

pub fn read_since<T: DeserializeOwned>(
    _jsonl: &ccb_storage::jsonl::JsonlStore,
    path: &camino::Utf8Path,
    record_type: &str,
    start_line: usize,
) -> crate::Result<(usize, Vec<T>)> {
    let file = std::fs::File::open(path)?;
    let reader = std::io::BufReader::new(file);
    let mut results = Vec::new();
    let mut current = 0usize;
    for line in std::io::BufRead::lines(reader) {
        let line = line?;
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        current += 1;
        if current <= start_line {
            continue;
        }
        let value: Value = serde_json::from_str(trimmed)?;
        results.push(from_value(record_type, value)?);
    }
    Ok((current, results))
}

pub fn find_last<T: DeserializeOwned>(
    _jsonl: &ccb_storage::jsonl::JsonlStore,
    path: &camino::Utf8Path,
    record_type: &str,
    predicate: impl Fn(&T) -> bool,
) -> crate::Result<Option<T>> {
    let rows = read_all(_jsonl, path, record_type)?;
    Ok(rows.into_iter().rev().find(predicate))
}

pub const MAILBOX_RECORD: &str = "mailbox_record";
pub const INBOUND_EVENT_RECORD: &str = "inbound_event_record";
pub const DELIVERY_LEASE: &str = "delivery_lease";
pub const MESSAGE_RECORD: &str = "message_record";
pub const ATTEMPT_RECORD: &str = "attempt_record";
pub const REPLY_RECORD: &str = "reply_record";
pub const CALLBACK_EDGE_RECORD: &str = "callback_edge";
