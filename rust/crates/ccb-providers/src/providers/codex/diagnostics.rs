//! Codex diagnostic-log filter.
//!
//! Mirrors Python `provider_backends/codex/launcher_runtime/command_runtime/diagnostics.py`.
//!
//! By default Codex writes a large volume of diagnostic rows to
//! `logs_2.sqlite`. This module installs a SQLite trigger that drops those
//! rows before they are inserted, and optionally redirects the DB to a temp
//! location so the user's real Codex home stays small.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::Connection;
use sha2::Digest;
use tracing::{debug, trace, warn};

const DB_NAME: &str = "logs_2.sqlite";
const TRIGGER_NAME: &str = "ccb_drop_diagnostic_logs";
const TRIGGER_SQL: &str =
    "CREATE TRIGGER ccb_drop_diagnostic_logs BEFORE INSERT ON logs BEGIN SELECT RAISE(IGNORE); END";

/// True when the user explicitly wants Codex diagnostic logs kept.
pub fn codex_diagnostic_logs_enabled() -> bool {
    matches!(
        std::env::var("CCB_CODEX_DIAGNOSTIC_LOGS")
            .unwrap_or_default()
            .trim()
            .to_lowercase()
            .as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// Ensure the managed Codex diagnostic-log policy is applied.
///
/// Returns `true` once the trigger is present and the redirect is in place.
/// `false` means the DB does not exist yet or is temporarily locked; callers
/// should retry later.
pub fn ensure_codex_diagnostic_log_filter(codex_home: &Path, runtime_dir: Option<&Path>) -> bool {
    if codex_diagnostic_logs_enabled() {
        return remove_codex_diagnostic_log_filter(codex_home);
    }
    let _ = ensure_codex_diagnostic_log_redirect(codex_home, runtime_dir);
    install_codex_diagnostic_log_filter(codex_home)
}

/// Install the diagnostic-log drop trigger in `codex_home/logs_2.sqlite`.
pub fn install_codex_diagnostic_log_filter(codex_home: &Path) -> bool {
    let db_path = codex_home.join(DB_NAME);
    if !db_path.is_file() {
        trace!(path = %db_path.display(), "codex log db does not exist yet");
        return false;
    }
    let conn =
        match Connection::open_with_flags(&db_path, rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE) {
            Ok(c) => c,
            Err(e) => {
                warn!(error = %e, path = %db_path.display(), "cannot open codex log db");
                return false;
            }
        };
    install_filter_in_connection(&conn, db_path)
}

fn install_filter_in_connection(conn: &Connection, db_path: PathBuf) -> bool {
    if !logs_table_exists(conn) {
        trace!(path = %db_path.display(), "codex log db has no logs table yet");
        return false;
    }
    let current = trigger_sql(conn);
    if trigger_sql_matches(current.as_deref()) {
        debug!(path = %db_path.display(), "codex diagnostic filter already installed");
        return true;
    }
    if let Err(e) = conn.execute(&format!("DROP TRIGGER IF EXISTS {}", TRIGGER_NAME), []) {
        warn!(error = %e, "failed to drop old diagnostic trigger");
    }
    if let Err(e) = conn.execute(TRIGGER_SQL, []) {
        warn!(error = %e, "failed to install diagnostic trigger");
        return false;
    }
    debug!(path = %db_path.display(), "installed codex diagnostic filter");
    true
}

/// Remove the trigger and restore the original DB location.
pub fn remove_codex_diagnostic_log_filter(codex_home: &Path) -> bool {
    restore_codex_diagnostic_log_redirect(codex_home);
    let db_path = codex_home.join(DB_NAME);
    if !db_path.is_file() {
        return true;
    }
    let Ok(conn) =
        Connection::open_with_flags(&db_path, rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE)
    else {
        return false;
    };
    let _ = conn.execute(&format!("DROP TRIGGER IF EXISTS {}", TRIGGER_NAME), []);
    true
}

/// Apply the filter using `CODEX_SQLITE_HOME` / `CODEX_HOME` env vars.
pub fn ensure_codex_diagnostic_log_filter_from_env() -> bool {
    let Some(home) = codex_home_from_env() else {
        return false;
    };
    let runtime_dir = std::env::var("CODEX_RUNTIME_DIR")
        .ok()
        .map(PathBuf::from)
        .filter(|p| !p.as_os_str().is_empty());
    ensure_codex_diagnostic_log_filter(&home, runtime_dir.as_deref())
}

/// Poll-based installer used by long-lived adapters.
#[derive(Debug)]
pub struct CodexDiagnosticLogFilterInstaller {
    ensured: bool,
    next_attempt: std::time::Instant,
    interval: Duration,
}

impl CodexDiagnosticLogFilterInstaller {
    pub fn new(interval: Duration) -> Self {
        Self {
            ensured: false,
            next_attempt: std::time::Instant::now(),
            interval: interval.max(Duration::from_millis(100)),
        }
    }

    pub fn maybe_install(&mut self) -> bool {
        if self.ensured {
            return true;
        }
        let now = std::time::Instant::now();
        if now < self.next_attempt {
            return false;
        }
        self.next_attempt = now + self.interval;
        self.ensured = ensure_codex_diagnostic_log_filter_from_env();
        self.ensured
    }
}

impl Default for CodexDiagnosticLogFilterInstaller {
    fn default() -> Self {
        Self::new(Duration::from_secs(5))
    }
}

// ---------------------------------------------------------------------------
// Redirect logic: symlink the real logs_2.sqlite to a temp location.
// ---------------------------------------------------------------------------

fn ensure_codex_diagnostic_log_redirect(codex_home: &Path, runtime_dir: Option<&Path>) -> bool {
    let home = expanduser(codex_home);
    let _ = fs::create_dir_all(&home);
    let db_path = home.join(DB_NAME);

    if db_path.is_symlink() {
        return repair_existing_log_db_symlink(&db_path);
    }

    // Move away any existing real DB / sidecars before creating the symlink.
    for sidecar in [format!("{}-wal", DB_NAME), format!("{}-shm", DB_NAME)] {
        let _ = move_path_to_backup(&home.join(sidecar));
    }
    let _ = move_path_to_backup(&db_path);

    let target = diagnostic_log_temp_db_path(&home, runtime_dir);
    let _ = fs::create_dir_all(target.parent().unwrap_or(&target));
    match std::os::unix::fs::symlink(&target, &db_path) {
        Ok(()) => true,
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => db_path.is_symlink(),
        Err(_) => {
            restore_diagnostic_log_backups(&home);
            false
        }
    }
}

fn restore_codex_diagnostic_log_redirect(codex_home: &Path) {
    let home = expanduser(codex_home);
    let db_path = home.join(DB_NAME);
    if !db_path.is_symlink() {
        return;
    }
    let _ = fs::remove_file(&db_path);
    restore_diagnostic_log_backups(&home);
}

fn repair_existing_log_db_symlink(db_path: &Path) -> bool {
    let Some(target) = symlink_target_path(db_path) else {
        return false;
    };
    let _ = fs::create_dir_all(target.parent().unwrap_or(&target));
    if target.exists() && !target.is_file() {
        return false;
    }
    true
}

fn diagnostic_log_temp_db_path(codex_home: &Path, runtime_dir: Option<&Path>) -> PathBuf {
    let root: PathBuf = if let Ok(raw) = std::env::var("CCB_CODEX_LOGS_TMPDIR") {
        expanduser(Path::new(&raw))
    } else {
        let uid = std::process::id();
        std::env::temp_dir().join(format!("ccb-codex-logs-{uid}"))
    };
    let digest_source = format!(
        "{}\n{}",
        codex_home
            .canonicalize()
            .unwrap_or_else(|_| codex_home.to_path_buf())
            .display(),
        runtime_dir
            .and_then(|p| p.canonicalize().ok())
            .unwrap_or_else(|| runtime_dir.unwrap_or(Path::new("")).to_path_buf())
            .display()
    );
    let digest = format!("{:x}", sha2::Sha256::digest(digest_source.as_bytes()));
    root.join(&digest[..16]).join(DB_NAME)
}

// ---------------------------------------------------------------------------
// Backup helpers
// ---------------------------------------------------------------------------

fn move_path_to_backup(path: &Path) -> Option<PathBuf> {
    if !path.exists() && !path.is_symlink() {
        return None;
    }
    let backup = next_backup_path(path);
    fs::rename(path, &backup).ok()?;
    Some(backup)
}

fn next_backup_path(path: &Path) -> PathBuf {
    let base = path.with_extension(format!(
        "{}.bak",
        path.extension().unwrap_or_default().to_string_lossy()
    ));
    if !base.exists() && !base.is_symlink() {
        return base;
    }
    for i in 1..1000 {
        let candidate = path.with_extension(format!(
            "{}.bak.{}",
            path.extension().unwrap_or_default().to_string_lossy(),
            i
        ));
        if !candidate.exists() && !candidate.is_symlink() {
            return candidate;
        }
    }
    path.with_extension(format!(
        "{}.bak.{}",
        path.extension().unwrap_or_default().to_string_lossy(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs()
    ))
}

fn restore_diagnostic_log_backups(home: &Path) {
    let db_path = home.join(DB_NAME);
    if !db_path.exists() && !db_path.is_symlink() {
        if let Some(backup) = existing_backup_path(&db_path) {
            let _ = fs::rename(backup, &db_path);
        }
    }
    for sidecar in [format!("{}-wal", DB_NAME), format!("{}-shm", DB_NAME)] {
        let sidecar_path = home.join(&sidecar);
        if !sidecar_path.exists() && !sidecar_path.is_symlink() {
            if let Some(backup) = existing_backup_path(&sidecar_path) {
                let _ = fs::rename(backup, &sidecar_path);
            }
        }
    }
}

fn existing_backup_path(path: &Path) -> Option<PathBuf> {
    let first = path.with_extension(format!(
        "{}.bak",
        path.extension().unwrap_or_default().to_string_lossy()
    ));
    if first.exists() || first.is_symlink() {
        return Some(first);
    }
    let mut backups: Vec<_> = path
        .parent()?
        .read_dir()
        .ok()?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            let name = p.file_name().and_then(|s| s.to_str()).unwrap_or("");
            name.starts_with(&format!(
                "{}.",
                path.file_name().unwrap_or_default().to_string_lossy()
            )) && name.contains(".bak")
        })
        .collect();
    backups.sort();
    backups.into_iter().next()
}

// ---------------------------------------------------------------------------
// SQLite introspection
// ---------------------------------------------------------------------------

fn logs_table_exists(conn: &Connection) -> bool {
    conn.query_row(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='logs' LIMIT 1",
        [],
        |_| Ok(true),
    )
    .unwrap_or(false)
}

fn trigger_sql(conn: &Connection) -> Option<String> {
    conn.query_row(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=? LIMIT 1",
        [TRIGGER_NAME],
        |row| row.get::<_, String>(0),
    )
    .ok()
}

fn trigger_sql_matches(sql: Option<&str>) -> bool {
    let Some(sql) = sql else { return false };
    let normalized = sql
        .to_lowercase()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    let compact = normalized.replace(' ', "");
    normalized.contains(&format!("create trigger {}", TRIGGER_NAME))
        && normalized.contains("before insert on logs")
        && compact.contains("raise(ignore)")
}

// ---------------------------------------------------------------------------
// Path helpers
// ---------------------------------------------------------------------------

fn codex_home_from_env() -> Option<PathBuf> {
    for key in ["CODEX_SQLITE_HOME", "CODEX_HOME"] {
        if let Ok(raw) = std::env::var(key) {
            let raw = raw.trim();
            if !raw.is_empty() {
                return Some(PathBuf::from(raw));
            }
        }
    }
    None
}

fn expanduser(path: &Path) -> PathBuf {
    if let Some(s) = path.to_str() {
        if let Some(rest) = s.strip_prefix("~/") {
            return std::env::var_os("HOME")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("/"))
                .join(rest);
        }
        if s == "~" {
            return std::env::var_os("HOME")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("/"));
        }
    }
    path.to_path_buf()
}

fn symlink_target_path(path: &Path) -> Option<PathBuf> {
    fs::read_link(path).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_trigger_sql_matches() {
        assert!(trigger_sql_matches(Some(TRIGGER_SQL)));
        assert!(!trigger_sql_matches(Some(
            "CREATE TRIGGER other BEFORE INSERT ON logs BEGIN SELECT RAISE(IGNORE); END"
        )));
        assert!(!trigger_sql_matches(None));
    }

    #[test]
    fn test_install_filter_creates_trigger() {
        let tmp = tempfile::tempdir().unwrap();
        let db_path = tmp.path().join(DB_NAME);
        let conn = Connection::open(&db_path).unwrap();
        conn.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY, msg TEXT)", [])
            .unwrap();
        drop(conn);

        assert!(install_codex_diagnostic_log_filter(tmp.path()));

        let conn = Connection::open(&db_path).unwrap();
        assert!(trigger_sql(&conn).is_some());
        // Inserting a row should be silently dropped by the trigger.
        conn.execute("INSERT INTO logs (msg) VALUES ('diagnostic')", [])
            .unwrap();
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM logs", [], |row| row.get(0))
            .unwrap();
        assert_eq!(count, 0);
    }
}
