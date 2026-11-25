//! Reliable FIFO delivery and ack/spool helpers.
//!
//! Mirrors Python `provider_core.fifo_delivery`:
//! - non-blocking FIFO writes with retry/backoff
//! - atomic ack files for read confirmation
//! - spool files for oversized payloads

use std::io;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// Outcome of a send attempt.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeliveryResult {
    /// Receiver confirmed reading the message (ack file observed).
    Delivered,
    /// Written, but no read confirmation arrived in time.
    Unconfirmed,
    /// Could not be written at all.
    Failed,
}

impl DeliveryResult {
    pub fn is_failed(self) -> bool {
        matches!(self, DeliveryResult::Failed)
    }
}

/// Atomic FIFO write limit. POSIX guarantees >= 512; Linux/macOS use 4096+.
pub const PIPE_ATOMIC_LIMIT: usize = 4096;

const RETRY_BACKOFFS: &[f64] = &[0.1, 0.3, 0.9];

/// The message could not be handed to the receiving end.
#[derive(Debug)]
pub struct CommDeliveryError(pub String);

impl std::fmt::Display for CommDeliveryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for CommDeliveryError {}

/// Write one newline-terminated line to a FIFO, retrying while the FIFO has no
/// reader (`ENXIO`). Raises `CommDeliveryError` after exhausting retries.
pub fn write_fifo_line(
    fifo_path: &Path,
    line: &str,
    backoffs: &[f64],
) -> Result<(), CommDeliveryError> {
    let mut data = line.as_bytes().to_vec();
    if !data.ends_with(b"\n") {
        data.push(b'\n');
    }
    if data.len() > PIPE_ATOMIC_LIMIT {
        return Err(CommDeliveryError(format!(
            "line of {} bytes exceeds atomic FIFO write limit ({}); spool the payload and send a pointer instead",
            data.len(),
            PIPE_ATOMIC_LIMIT
        )));
    }

    let backoffs = if backoffs.is_empty() {
        RETRY_BACKOFFS
    } else {
        backoffs
    };
    let attempts = backoffs.len() + 1;
    let mut last_error = String::new();

    for attempt in 0..attempts {
        let fd = match open_fifo_write_nonblock(fifo_path) {
            Ok(fd) => fd,
            Err(e) if e.raw_os_error() == Some(libc::ENXIO) => {
                last_error = format!("no reader: {}", e);
                if attempt < backoffs.len() {
                    std::thread::sleep(Duration::from_secs_f64(backoffs[attempt]));
                }
                continue;
            }
            Err(e) => {
                return Err(CommDeliveryError(format!(
                    "cannot open {}: {}",
                    fifo_path.display(),
                    e
                )))
            }
        };

        let written = unsafe { libc::write(fd, data.as_ptr() as *const libc::c_void, data.len()) };
        let write_err = if written < 0 {
            Some(io::Error::last_os_error())
        } else if written as usize != data.len() {
            Some(io::Error::other(format!(
                "partial FIFO write to {}: {}/{}",
                fifo_path.display(),
                written,
                data.len()
            )))
        } else {
            None
        };
        unsafe { libc::close(fd) };

        if let Some(e) = write_err {
            if e.kind() == io::ErrorKind::WouldBlock {
                last_error = format!("would block: {}", e);
                if attempt < backoffs.len() {
                    std::thread::sleep(Duration::from_secs_f64(backoffs[attempt]));
                }
                continue;
            }
            return Err(CommDeliveryError(format!(
                "write to {} failed: {}",
                fifo_path.display(),
                e
            )));
        }
        return Ok(());
    }

    Err(CommDeliveryError(format!(
        "receiver not listening on {} after {} attempts ({})",
        fifo_path.display(),
        attempts,
        last_error
    )))
}

fn open_fifo_write_nonblock(path: &Path) -> io::Result<libc::c_int> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    let cpath = CString::new(path.as_os_str().as_bytes())
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidInput, e))?;
    let fd = unsafe { libc::open(cpath.as_ptr(), libc::O_WRONLY | libc::O_NONBLOCK) };
    if fd < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(fd)
    }
}

/// Path to the ack file for a given marker.
pub fn ack_file_path(ack_dir: &Path, marker: &str) -> PathBuf {
    ack_dir.join(format!("{}.ack", marker))
}

/// Atomically record that a message was read; best-effort, never raises.
pub fn write_ack(ack_dir: &Path, marker: &str) {
    let _ = std::fs::create_dir_all(ack_dir);
    let target = ack_file_path(ack_dir, marker);
    let tmp = target.with_extension("ack.tmp");
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64();
    if std::fs::write(&tmp, format!("{}", now)).is_ok() {
        let _ = std::fs::rename(&tmp, &target);
    }
}

/// Poll for the receiver's read confirmation. Returns `true` if the ack file
/// appears in time. Removes the ack file on success.
pub fn wait_for_ack(ack_dir: &Path, marker: &str, timeout: Duration) -> bool {
    let target = ack_file_path(ack_dir, marker);
    let deadline = Instant::now() + timeout;
    let mut interval = Duration::from_millis(50);
    loop {
        if target.exists() {
            let _ = std::fs::remove_file(&target);
            return true;
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return false;
        }
        std::thread::sleep(std::cmp::min(interval, remaining));
        interval = std::cmp::min(interval.mul_f32(1.5), Duration::from_millis(500));
    }
}

/// Drop stale ack files.
pub fn cleanup_acks(ack_dir: &Path, max_age: Duration) {
    let Ok(cutoff) = SystemTime::now().duration_since(UNIX_EPOCH) else {
        return;
    };
    let cutoff = cutoff.saturating_sub(max_age).as_secs_f64();
    let Ok(entries) = ack_dir.read_dir() else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|s| s.to_str()) != Some("ack") {
            continue;
        }
        let stale = match std::fs::read_to_string(&path) {
            Ok(s) => s.trim().parse::<f64>().unwrap_or(f64::MAX) < cutoff,
            Err(_) => false,
        };
        if stale {
            let _ = std::fs::remove_file(&path);
        }
    }
}

/// Persist an oversized payload and return the spool file path.
pub fn spool_payload(spool_dir: &Path, marker: &str, payload_json: &str) -> PathBuf {
    let _ = std::fs::create_dir_all(spool_dir);
    let target = spool_dir.join(format!("{}.json", marker));
    let tmp = spool_dir.join(format!("{}.json.tmp", marker));
    if std::fs::write(&tmp, payload_json).is_ok() {
        let _ = std::fs::rename(&tmp, &target);
    }
    target
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ack_round_trip() {
        let tmp = std::env::temp_dir().join(format!("ccb-test-ack-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(&tmp).unwrap();

        write_ack(&tmp, "marker-1");
        assert!(ack_file_path(&tmp, "marker-1").exists());
        assert!(wait_for_ack(&tmp, "marker-1", Duration::from_secs(1)));
        assert!(!ack_file_path(&tmp, "marker-1").exists());

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_spool_payload() {
        let tmp = std::env::temp_dir().join(format!("ccb-test-spool-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);

        let path = spool_payload(&tmp, "m1", r#"{"content":"big"}"#);
        assert!(path.exists());
        assert_eq!(
            std::fs::read_to_string(&path).unwrap(),
            r#"{"content":"big"}"#
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_cleanup_acks() {
        let tmp = std::env::temp_dir().join(format!("ccb-test-cleanup-ack-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(&tmp).unwrap();

        write_ack(&tmp, "fresh");
        // Manually create a stale ack with an old timestamp.
        let stale_path = ack_file_path(&tmp, "stale");
        std::fs::write(&stale_path, "1.0").unwrap();

        cleanup_acks(&tmp, Duration::from_secs(3600));
        assert!(!stale_path.exists());
        assert!(ack_file_path(&tmp, "fresh").exists());

        let _ = std::fs::remove_dir_all(&tmp);
    }
}
