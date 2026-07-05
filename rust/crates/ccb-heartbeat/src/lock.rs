use camino::Utf8Path;
use fs2::FileExt;
use std::fs;
use std::io::{self, Seek, SeekFrom, Write};

#[derive(Debug, thiserror::Error)]
#[error("maintenance heartbeat tick is already running")]
pub struct MaintenanceHeartbeatLockBusy;

pub struct MaintenanceHeartbeatLock {
    file: fs::File,
    payload: serde_json::Value,
    released: bool,
}

impl MaintenanceHeartbeatLock {
    pub fn try_acquire(
        path: &Utf8Path,
        payload: serde_json::Value,
    ) -> Result<Self, MaintenanceHeartbeatLockBusy> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|_| MaintenanceHeartbeatLockBusy)?;
        }
        let file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .read(true)
            .open(path)
            .map_err(|_| MaintenanceHeartbeatLockBusy)?;
        if file.try_lock_exclusive().is_err() {
            return Err(MaintenanceHeartbeatLockBusy);
        }
        let mut lock = Self {
            file,
            payload,
            released: false,
        };
        lock.write_state(true)
            .map_err(|_| MaintenanceHeartbeatLockBusy)?;
        Ok(lock)
    }

    pub fn release(&mut self) -> io::Result<()> {
        if self.released {
            return Ok(());
        }
        self.write_state(false)?;
        self.file.unlock()?;
        self.released = true;
        Ok(())
    }

    fn write_state(&mut self, held: bool) -> io::Result<()> {
        let mut state = if let serde_json::Value::Object(map) = &self.payload {
            map.clone()
        } else {
            serde_json::Map::new()
        };
        state.insert("held".into(), held.into());
        if !held {
            state.insert("released_at".into(), serde_json::Value::Null);
        }
        let text = serde_json::to_string_pretty(&serde_json::Value::Object(state))? + "\n";
        self.file.set_len(0)?;
        self.file.seek(SeekFrom::Start(0))?;
        self.file.write_all(text.as_bytes())?;
        self.file.flush()?;
        self.file.sync_all()?;
        Ok(())
    }
}

impl Drop for MaintenanceHeartbeatLock {
    fn drop(&mut self) {
        let _ = self.release();
    }
}
