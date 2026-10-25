use camino::Utf8Path;
use fs2::FileExt;
use std::fs;
use std::io;

/// RAII file lock guard. Acquires exclusive flock on creation, releases on drop.
/// Mirrors Python `storage.locks.file_lock`.
pub struct FileLock {
    file: fs::File,
}

impl FileLock {
    pub fn acquire(path: &Utf8Path) -> io::Result<Self> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let file = fs::OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(path)?;
        file.lock_exclusive()?;
        Ok(Self { file })
    }
}

impl Drop for FileLock {
    fn drop(&mut self) {
        let _ = self.file.unlock();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_lock_acquire_release() {
        let dir = TempDir::new().unwrap();
        let p = dir.path().join("test.lock");
        let lock_path = Utf8Path::from_path(&p).unwrap();
        {
            let _lock = FileLock::acquire(lock_path).unwrap();
            // lock is held
        }
        // lock is released, can acquire again
        let _lock2 = FileLock::acquire(lock_path).unwrap();
    }
}
