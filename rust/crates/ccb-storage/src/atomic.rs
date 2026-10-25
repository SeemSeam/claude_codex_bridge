use camino::Utf8Path;
use serde::Serialize;
use std::fs;
use std::io::Write;
use uuid::Uuid;

/// Serialize a value as JSON with Python-compatible 2-space indentation.
pub fn to_json_pretty_2<T: Serialize>(value: &T) -> crate::Result<String> {
    let mut buf = Vec::new();
    let formatter = serde_json::ser::PrettyFormatter::with_indent(b"  ");
    let mut ser = serde_json::Serializer::with_formatter(&mut buf, formatter);
    value.serialize(&mut ser)?;
    // serde_json guarantees UTF-8 output.
    Ok(String::from_utf8(buf).expect("json serializer produced invalid utf-8"))
}

/// Write text to `path` atomically using a temp file + rename.
/// Mirrors Python `storage.atomic.atomic_write_text`.
pub fn atomic_write_text(path: &Utf8Path, text: &str) -> std::io::Result<()> {
    let parent = path.parent().ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::InvalidInput, "path has no parent")
    })?;
    fs::create_dir_all(parent)?;

    let file_name = path.file_name().unwrap_or("tmp");
    let tmp_name = format!(".{}.{:x}.tmp", file_name, Uuid::new_v4().as_simple());
    let tmp_path = parent.join(tmp_name);

    let result = write_and_sync(&tmp_path, text);
    if result.is_err() {
        let _ = fs::remove_file(&tmp_path);
    }
    result?;

    fs::rename(&tmp_path, path)?;
    Ok(())
}

fn write_and_sync(path: &Utf8Path, text: &str) -> std::io::Result<()> {
    let mut file = fs::File::create(path)?;
    file.write_all(text.as_bytes())?;
    file.sync_all()?;
    Ok(())
}

/// Serialize `value` as pretty JSON (2-space indent, unicode, trailing newline)
/// and atomically write it to `path`.
/// Mirrors Python `storage.atomic.atomic_write_json`.
pub fn atomic_write_json<T: Serialize>(path: &Utf8Path, value: &T) -> crate::Result<()> {
    let json = to_json_pretty_2(value)? + "\n";
    atomic_write_text(path, &json)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Serialize;
    use std::io::Read;
    use tempfile::TempDir;

    #[derive(Serialize)]
    struct Sample {
        name: String,
        value: i32,
    }

    #[test]
    fn test_atomic_write_text_round_trip() {
        let dir = TempDir::new().unwrap();
        let p = dir.path().join("target.txt");
        let path = Utf8Path::from_path(&p).unwrap();
        atomic_write_text(path, "hello world").unwrap();
        assert_eq!(fs::read_to_string(path).unwrap(), "hello world");
    }

    #[test]
    fn test_atomic_write_json_format() {
        let dir = TempDir::new().unwrap();
        let p = dir.path().join("target.json");
        let path = Utf8Path::from_path(&p).unwrap();
        atomic_write_json(
            path,
            &Sample {
                name: "test".into(),
                value: 42,
            },
        )
        .unwrap();
        let mut content = String::new();
        fs::File::open(path)
            .unwrap()
            .read_to_string(&mut content)
            .unwrap();
        assert!(
            content.contains("  \"name\": \"test\""),
            "content: {content}"
        );
        assert!(content.ends_with('\n'));
    }

    #[test]
    fn test_atomic_write_creates_parent_dirs() {
        let dir = TempDir::new().unwrap();
        let p = dir.path().join("a/b/c/nested.json");
        let path = Utf8Path::from_path(&p).unwrap();
        atomic_write_text(path, "{}").unwrap();
        assert!(path.exists());
    }
}
