//! Cross-platform message transport for provider-daemon communication.
//!
//! Mirrors Python `provider_core.transport`:
//! - POSIX: named pipe (`FifoTransport`) with a persistent reader.
//! - Windows: atomically-renamed message files in an inbox dir (`SpoolDirTransport`).

use std::io;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use crate::fifo_delivery::write_fifo_line;

/// One-directional line transport between a sender and a single reader.
pub trait MessageTransport: Send + Sync {
    /// Deliver one JSON line. Returns `Ok(())` once the bytes are handed to the
    /// transport, or an error if delivery is impossible.
    fn send_line(&self, line: &str) -> io::Result<()>;

    /// Wait up to `timeout` for the next line, returning `None` on idle.
    fn read_line(&mut self, timeout: Duration) -> io::Result<Option<String>>;

    /// Release reader-side resources only. The sender side is stateless.
    fn close(&mut self) {}
}

/// POSIX named-pipe transport. The reader holds the FIFO open persistently so
/// that writers never see a window with no reader (which can cause blocking or
/// lost messages).
pub struct FifoTransport {
    path: PathBuf,
    reader: Option<PersistentFifoReader>,
}

impl FifoTransport {
    pub fn new(fifo_path: impl Into<PathBuf>) -> Self {
        Self {
            path: fifo_path.into(),
            reader: None,
        }
    }
}

impl MessageTransport for FifoTransport {
    fn send_line(&self, line: &str) -> io::Result<()> {
        write_fifo_line(&self.path, line, &[]).map_err(|e| io::Error::other(e.to_string()))
    }

    fn read_line(&mut self, timeout: Duration) -> io::Result<Option<String>> {
        if self.reader.is_none() {
            self.reader = Some(PersistentFifoReader::new(&self.path)?);
        }
        self.reader.as_mut().unwrap().read_line(timeout)
    }

    fn close(&mut self) {
        if let Some(reader) = self.reader.take() {
            reader.close();
        }
    }
}

/// Windows-style inbox-directory transport. Each message is one file that is
/// atomically renamed into the inbox; the reader consumes files in sorted
/// order. This is the fallback on platforms without `os.mkfifo`.
pub struct SpoolDirTransport {
    inbox: PathBuf,
}

impl SpoolDirTransport {
    pub fn new(inbox_dir: impl Into<PathBuf>) -> Self {
        Self {
            inbox: inbox_dir.into(),
        }
    }
}

impl MessageTransport for SpoolDirTransport {
    fn send_line(&self, line: &str) -> io::Result<()> {
        use std::fs;
        use std::sync::atomic::{AtomicU64, Ordering};
        use std::time::{SystemTime, UNIX_EPOCH};

        static COUNTER: AtomicU64 = AtomicU64::new(0);

        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let count = COUNTER.fetch_add(1, Ordering::Relaxed);
        let pid = std::process::id();
        let name = format!("{nanos:020}-{pid}-{count:06}");

        fs::create_dir_all(&self.inbox)?;
        let tmp = self.inbox.join(format!("{name}.msg.tmp"));
        let final_path = self.inbox.join(format!("{name}.msg"));
        fs::write(&tmp, line)?;
        fs::rename(&tmp, final_path)
    }

    fn read_line(&mut self, timeout: Duration) -> io::Result<Option<String>> {
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(line) = next_spool_entry(&self.inbox)? {
                return Ok(Some(line));
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Ok(None);
            }
            std::thread::sleep(std::cmp::min(Duration::from_millis(200), remaining));
        }
    }
}

fn next_spool_entry(inbox: &Path) -> io::Result<Option<String>> {
    let mut entries: Vec<PathBuf> = match std::fs::read_dir(inbox) {
        Ok(iter) => iter
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.extension().and_then(|s| s.to_str()) == Some("msg"))
            .collect(),
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e),
    };
    entries.sort();
    for path in entries {
        let line = match std::fs::read_to_string(&path) {
            Ok(s) => s,
            Err(_) => continue,
        };
        let _ = std::fs::remove_file(&path);
        return Ok(Some(line));
    }
    Ok(None)
}

/// Holds the FIFO read end open for the lifetime of the transport.
///
/// Mirrors Python `provider_backends.codex.bridge_runtime.runtime_io.PersistentFifoReader`.
pub struct PersistentFifoReader {
    path: PathBuf,
    read_fd: Option<libc::c_int>,
    keepalive_fd: Option<libc::c_int>,
    buffer: Vec<u8>,
}

impl PersistentFifoReader {
    pub fn new(fifo_path: impl Into<PathBuf>) -> io::Result<Self> {
        Ok(Self {
            path: fifo_path.into(),
            read_fd: None,
            keepalive_fd: None,
            buffer: Vec::new(),
        })
    }

    pub fn read_line(&mut self, timeout: Duration) -> io::Result<Option<String>> {
        if let Some(line) = pop_line(&mut self.buffer) {
            return Ok(Some(line));
        }
        self.ensure_open()?;
        let fd = self.read_fd.expect("ensure_open did not set read_fd");

        let mut pollfd = libc::pollfd {
            fd,
            events: libc::POLLIN,
            revents: 0,
        };
        let ms = timeout.as_millis().min(i32::MAX as u128) as i32;
        let ready = unsafe { libc::poll(&mut pollfd, 1, ms) };
        if ready <= 0 {
            return Ok(None);
        }

        let mut chunk = [0u8; 65536];
        let n = unsafe { libc::read(fd, chunk.as_mut_ptr() as *mut libc::c_void, chunk.len()) };
        if n < 0 {
            return Err(io::Error::last_os_error());
        }
        if n == 0 {
            return Ok(None);
        }
        self.buffer.extend_from_slice(&chunk[..n as usize]);
        Ok(pop_line(&mut self.buffer))
    }

    pub fn close(mut self) {
        Self::close_internal(&mut self);
    }

    fn ensure_open(&mut self) -> io::Result<()> {
        if self.read_fd.is_some() {
            return Ok(());
        }
        if !self.path.exists() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!("FIFO does not exist: {}", self.path.display()),
            ));
        }

        let read_fd = unsafe { libc::open(c_str(&self.path), libc::O_RDONLY | libc::O_NONBLOCK) };
        if read_fd < 0 {
            return Err(io::Error::last_os_error());
        }

        // Keepalive write end: safe because this process already holds a reader.
        let keepalive_fd = unsafe { libc::open(c_str(&self.path), libc::O_WRONLY) };
        if keepalive_fd < 0 {
            unsafe { libc::close(read_fd) };
            return Err(io::Error::last_os_error());
        }

        self.read_fd = Some(read_fd);
        self.keepalive_fd = Some(keepalive_fd);
        Ok(())
    }

    fn close_internal(&mut self) {
        for attr in [&mut self.read_fd, &mut self.keepalive_fd] {
            if let Some(fd) = attr.take() {
                unsafe { libc::close(fd) };
            }
        }
    }
}

impl Drop for PersistentFifoReader {
    fn drop(&mut self) {
        Self::close_internal(self);
    }
}

fn pop_line(buffer: &mut Vec<u8>) -> Option<String> {
    if let Some(pos) = buffer.iter().position(|&b| b == b'\n') {
        let raw: Vec<u8> = buffer.drain(..=pos).collect();
        let text = String::from_utf8_lossy(&raw[..raw.len() - 1]);
        Some(text.into_owned())
    } else {
        None
    }
}

fn c_str(path: &Path) -> *const libc::c_char {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;
    use std::sync::Mutex;

    // Leak a stable C string; this helper is only used for FIFO paths that are
    // opened once per reader lifetime.
    static BUF: Mutex<Vec<CString>> = Mutex::new(Vec::new());
    let cstring = CString::new(path.as_os_str().as_bytes()).unwrap();
    let ptr = cstring.as_ptr();
    BUF.lock().unwrap().push(cstring);
    ptr
}

/// Map a configured FIFO path to this platform's actual endpoint.
///
/// POSIX: the FIFO itself. Windows: an `inbox` directory next to it.
pub fn endpoint_for_fifo_path(fifo_path: &Path) -> PathBuf {
    #[cfg(unix)]
    {
        fifo_path.to_path_buf()
    }
    #[cfg(not(unix))]
    {
        fifo_path.parent().unwrap_or(fifo_path).join("inbox")
    }
}

/// Sole platform-decision point: FIFO on POSIX, inbox dir on Windows.
pub fn create_transport(endpoint: &Path) -> Box<dyn MessageTransport> {
    #[cfg(unix)]
    {
        Box::new(FifoTransport::new(endpoint))
    }
    #[cfg(not(unix))]
    {
        Box::new(SpoolDirTransport::new(endpoint))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_endpoint_for_fifo_path_posix() {
        let fifo = Path::new("/tmp/ccb/input.fifo");
        assert_eq!(endpoint_for_fifo_path(fifo), fifo);
    }

    #[test]
    fn test_spool_dir_transport_round_trip() {
        let tmp = std::env::temp_dir().join(format!("ccb-test-spool-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);

        let sender = SpoolDirTransport::new(&tmp);
        let mut reader = SpoolDirTransport::new(&tmp);

        sender.send_line("first").unwrap();
        sender.send_line("second").unwrap();

        assert_eq!(
            reader.read_line(Duration::from_secs(1)).unwrap(),
            Some("first".to_string())
        );
        assert_eq!(
            reader.read_line(Duration::from_secs(1)).unwrap(),
            Some("second".to_string())
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }
}
