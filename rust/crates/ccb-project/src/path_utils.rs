use camino::{Utf8Path, Utf8PathBuf};
use std::path::{Path, PathBuf};

/// Expand a leading `~` using the `HOME` environment variable.
pub(crate) fn expand_user_path(raw: &str) -> String {
    if let Some(rest) = raw.strip_prefix('~') {
        if let Ok(home) = std::env::var("HOME") {
            return home + rest;
        }
    }
    raw.to_string()
}

/// Return an absolute version of `path`. Relative paths are anchored to the
/// current working directory.
pub(crate) fn absolute_path(path: &Path) -> PathBuf {
    if path.is_absolute() {
        return path.to_path_buf();
    }
    std::env::current_dir()
        .map(|cwd| cwd.join(path))
        .unwrap_or_else(|_| path.to_path_buf())
}

/// Resolve a path like Python's `Path.resolve()` with non-strict fallback:
/// canonicalize if the path exists, otherwise make it absolute.
pub(crate) fn resolve_utf8_path(path: &Utf8Path) -> Utf8PathBuf {
    let expanded = expand_user_path(path.as_str());
    let sys_path = PathBuf::from(&expanded);

    if let Ok(resolved) = std::fs::canonicalize(&sys_path) {
        if let Ok(utf8) = Utf8PathBuf::from_path_buf(resolved) {
            return utf8;
        }
    }

    let absolute = absolute_path(&sys_path);
    Utf8PathBuf::from_path_buf(absolute).unwrap_or_else(|_| Utf8PathBuf::from(expanded))
}

/// Manual POSIX-style path normalization that matches Python's
/// `posixpath.normpath`.
pub(crate) fn normalize_posix_path(value: &str) -> String {
    if let Some(rest) = value.strip_prefix("//") {
        let norm = normalize_posix_path(rest);
        return format!("//{}", norm.trim_start_matches('/'));
    }

    let absolute = value.starts_with('/');
    let mut stack: Vec<String> = Vec::new();
    for part in value.split('/') {
        if part.is_empty() || part == "." {
            continue;
        }
        if part == ".." {
            if let Some(top) = stack.last() {
                if top != ".." {
                    stack.pop();
                    continue;
                }
            }
            if !absolute {
                stack.push("..".to_string());
            }
            continue;
        }
        stack.push(part.to_string());
    }

    let joined = stack.join("/");
    if absolute {
        format!("/{joined}")
    } else {
        joined
    }
}

/// True when `value` looks like a Windows drive-letter path.
pub(crate) fn is_win_drive_path(value: &str) -> bool {
    value.len() >= 2
        && value.as_bytes()[0].is_ascii_alphabetic()
        && value.as_bytes()[1] == b':'
        && (value.len() == 2 || value.as_bytes()[2] == b'/' || value.as_bytes()[2] == b'\\')
}

/// Convert `/mnt/X/rest` to `x:/rest`.
pub(crate) fn normalize_mnt_drive_mapping(value: &str) -> Option<String> {
    let rest = value.strip_prefix("/mnt/")?;
    let drive = rest.chars().next()?;
    if !drive.is_ascii_alphabetic() {
        return None;
    }
    let after = &rest[drive.len_utf8()..];
    if after.is_empty() {
        return Some(format!("{}:/", drive.to_ascii_lowercase()));
    }
    if after.starts_with('/') {
        return Some(format!("{}:{}", drive.to_ascii_lowercase(), after));
    }
    None
}

/// Convert `/X/rest` to `x:/rest` when running under MSYS/Windows.
pub(crate) fn normalize_msys_drive_mapping(value: &str) -> Option<String> {
    if value.len() < 2 || value.as_bytes()[0] != b'/' {
        return None;
    }
    let drive = value.chars().nth(1)?;
    if !drive.is_ascii_alphabetic() {
        return None;
    }
    if std::env::var("MSYSTEM").is_ok() || std::env::consts::OS == "windows" {
        let after = &value[1 + drive.len_utf8()..];
        if after.starts_with('/') {
            return Some(format!("{}:{}", drive.to_ascii_lowercase(), after));
        }
    }
    None
}
