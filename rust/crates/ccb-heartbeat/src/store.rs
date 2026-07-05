use camino::Utf8PathBuf;
use ccb_storage::json::JsonStore;
use ccb_storage::jsonl::JsonlStore;
use ccb_storage::paths::PathLayout;
use ccb_storage::StorageError;

use crate::models::{
    HeartbeatState, MaintenanceHeartbeatActivation, MaintenanceHeartbeatRunner,
    MaintenanceHeartbeatSchedule, MaintenanceHeartbeatStatus,
};

pub struct HeartbeatStateStore {
    layout: PathLayout,
    json_store: JsonStore,
}

impl std::fmt::Debug for HeartbeatStateStore {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("HeartbeatStateStore")
            .field("layout", &self.layout)
            .finish_non_exhaustive()
    }
}

impl HeartbeatStateStore {
    pub fn new(layout: PathLayout) -> Self {
        Self::with_store(layout, None)
    }

    pub fn with_store(layout: PathLayout, json_store: Option<JsonStore>) -> Self {
        Self {
            layout,
            json_store: json_store.unwrap_or_default(),
        }
    }

    pub fn load(
        &self,
        subject_kind: &str,
        subject_id: &str,
    ) -> Result<Option<HeartbeatState>, StorageError> {
        let path = self
            .layout
            .heartbeat_subject_path(subject_kind, subject_id)?;
        if !path.exists() {
            return Ok(None);
        }
        let record: serde_json::Value = self.json_store.load(&path)?;
        HeartbeatState::from_record(record)
            .map(Some)
            .map_err(StorageError::Corrupt)
    }

    pub fn save(&self, state: &HeartbeatState) -> Result<(), StorageError> {
        let path = self
            .layout
            .heartbeat_subject_path(&state.subject_kind, &state.subject_id)?;
        self.json_store.save(&path, &state.to_record())
    }

    pub fn remove(&self, subject_kind: &str, subject_id: &str) -> Result<(), StorageError> {
        let path = self
            .layout
            .heartbeat_subject_path(subject_kind, subject_id)?;
        if path.exists() {
            std::fs::remove_file(&path)?;
        }
        Ok(())
    }

    pub fn list_all(
        &self,
        subject_kind: Option<&str>,
    ) -> Result<Vec<HeartbeatState>, StorageError> {
        let roots: Vec<Utf8PathBuf> = if let Some(kind) = subject_kind {
            let dir = self.layout.heartbeat_subject_dir(kind)?;
            if !dir.exists() {
                return Ok(Vec::new());
            }
            vec![dir]
        } else {
            let root = self.layout.ccbd_heartbeats_dir();
            if !root.exists() {
                return Ok(Vec::new());
            }
            root.read_dir()?
                .filter_map(|e| e.ok())
                .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false))
                .filter_map(|e| Utf8PathBuf::from_path_buf(e.path()).ok())
                .collect()
        };

        let mut states = Vec::new();
        for dir in roots {
            let mut entries: Vec<Utf8PathBuf> = std::fs::read_dir(&dir)?
                .filter_map(|e| e.ok())
                .filter(|e| {
                    e.path()
                        .extension()
                        .map(|ext| ext == "json")
                        .unwrap_or(false)
                })
                .filter_map(|e| Utf8PathBuf::from_path_buf(e.path()).ok())
                .collect();
            entries.sort();
            for path in entries {
                let record: serde_json::Value = self.json_store.load(&path)?;
                if let Ok(state) = HeartbeatState::from_record(record) {
                    states.push(state);
                }
            }
        }
        Ok(states)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReadState {
    Missing,
    Ok,
    Corrupt,
}

impl ReadState {
    pub fn as_str(&self) -> &'static str {
        match self {
            ReadState::Missing => "missing",
            ReadState::Ok => "ok",
            ReadState::Corrupt => "corrupt",
        }
    }
}

pub trait Record {
    fn to_record(&self) -> serde_json::Value;
}

impl Record for MaintenanceHeartbeatSchedule {
    fn to_record(&self) -> serde_json::Value {
        crate::models::MaintenanceHeartbeatSchedule::to_record(self)
    }
}

impl Record for MaintenanceHeartbeatStatus {
    fn to_record(&self) -> serde_json::Value {
        crate::models::MaintenanceHeartbeatStatus::to_record(self)
    }
}

impl Record for MaintenanceHeartbeatRunner {
    fn to_record(&self) -> serde_json::Value {
        crate::models::MaintenanceHeartbeatRunner::to_record(self)
    }
}

#[derive(Debug, Clone)]
pub struct MaintenanceHeartbeatReadResult<T> {
    pub state: ReadState,
    pub path: String,
    pub value: Option<T>,
    pub error: Option<String>,
}

impl<T: Record> MaintenanceHeartbeatReadResult<T> {
    pub fn to_record(&self) -> serde_json::Value {
        let mut record = serde_json::Map::new();
        record.insert("state".into(), self.state.as_str().into());
        record.insert("path".into(), self.path.clone().into());
        record.insert(
            "error".into(),
            self.error
                .clone()
                .map_or(serde_json::Value::Null, |e| e.into()),
        );
        if let Some(value) = &self.value {
            record.insert("record".into(), value.to_record());
        }
        serde_json::Value::Object(record)
    }
}

pub trait HasProjectId {
    fn project_id(&self) -> &str;
}

impl HasProjectId for MaintenanceHeartbeatSchedule {
    fn project_id(&self) -> &str {
        &self.project_id
    }
}

impl HasProjectId for MaintenanceHeartbeatStatus {
    fn project_id(&self) -> &str {
        &self.project_id
    }
}

impl HasProjectId for MaintenanceHeartbeatRunner {
    fn project_id(&self) -> &str {
        &self.project_id
    }
}

pub struct MaintenanceHeartbeatStore {
    layout: PathLayout,
    project_id: String,
    json_store: JsonStore,
    jsonl_store: JsonlStore,
}

impl std::fmt::Debug for MaintenanceHeartbeatStore {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("MaintenanceHeartbeatStore")
            .field("layout", &self.layout)
            .field("project_id", &self.project_id)
            .finish_non_exhaustive()
    }
}

impl MaintenanceHeartbeatStore {
    pub fn new(layout: PathLayout, project_id: &str) -> Result<Self, StorageError> {
        Self::with_stores(layout, project_id, None, None).map_err(|e| match e {
            crate::HeartbeatError::Storage(storage_err) => storage_err,
            crate::HeartbeatError::Validation(msg) => StorageError::Corrupt(msg),
        })
    }

    pub fn with_stores(
        layout: PathLayout,
        project_id: &str,
        json_store: Option<JsonStore>,
        jsonl_store: Option<JsonlStore>,
    ) -> crate::Result<Self> {
        let project_id = project_id.trim().to_string();
        if project_id.is_empty() {
            return Err(crate::HeartbeatError::Validation(
                "project_id cannot be empty".into(),
            ));
        }
        Ok(Self {
            layout,
            project_id,
            json_store: json_store.unwrap_or_default(),
            jsonl_store: jsonl_store.unwrap_or_default(),
        })
    }

    pub fn load_schedule(&self) -> MaintenanceHeartbeatReadResult<MaintenanceHeartbeatSchedule> {
        self.load(
            &self.layout.ccbd_maintenance_heartbeat_schedule_path(),
            MaintenanceHeartbeatSchedule::from_record,
        )
    }

    pub fn save_schedule(
        &self,
        schedule: &MaintenanceHeartbeatSchedule,
    ) -> Result<(), StorageError> {
        self.ensure_project(&schedule.project_id)?;
        self.json_store.save(
            &self.layout.ccbd_maintenance_heartbeat_schedule_path(),
            &schedule.to_record(),
        )
    }

    pub fn load_status(&self) -> MaintenanceHeartbeatReadResult<MaintenanceHeartbeatStatus> {
        self.load(
            &self.layout.ccbd_maintenance_heartbeat_status_path(),
            MaintenanceHeartbeatStatus::from_record,
        )
    }

    pub fn save_status(&self, status: &MaintenanceHeartbeatStatus) -> Result<(), StorageError> {
        self.ensure_project(&status.project_id)?;
        self.json_store.save(
            &self.layout.ccbd_maintenance_heartbeat_status_path(),
            &status.to_record(),
        )
    }

    pub fn load_runner(&self) -> MaintenanceHeartbeatReadResult<MaintenanceHeartbeatRunner> {
        self.load(
            &self.layout.ccbd_maintenance_heartbeat_runner_path(),
            MaintenanceHeartbeatRunner::from_record,
        )
    }

    pub fn save_runner(&self, runner: &MaintenanceHeartbeatRunner) -> Result<(), StorageError> {
        self.ensure_project(&runner.project_id)?;
        self.json_store.save(
            &self.layout.ccbd_maintenance_heartbeat_runner_path(),
            &runner.to_record(),
        )
    }

    pub fn append_activation(
        &self,
        activation: &MaintenanceHeartbeatActivation,
    ) -> Result<(), StorageError> {
        self.ensure_project(&activation.project_id)?;
        self.jsonl_store.append(
            &self.layout.ccbd_maintenance_heartbeat_activations_path(),
            &activation.to_record(),
        )
    }

    pub fn load_activation_tail(
        &self,
        limit: usize,
    ) -> Result<Vec<MaintenanceHeartbeatActivation>, StorageError> {
        let path = self.layout.ccbd_maintenance_heartbeat_activations_path();
        if !path.exists() {
            return Ok(Vec::new());
        }
        let rows: Vec<serde_json::Value> = self.jsonl_store.read_tail(&path, limit)?;
        let mut activations = Vec::new();
        for row in rows {
            match MaintenanceHeartbeatActivation::from_record(row) {
                Ok(activation) => {
                    if self.ensure_project(&activation.project_id).is_ok() {
                        activations.push(activation);
                    }
                }
                Err(_) => continue,
            }
        }
        Ok(activations)
    }

    fn load<T, F>(&self, path: &camino::Utf8Path, loader: F) -> MaintenanceHeartbeatReadResult<T>
    where
        T: HasProjectId + Record,
        F: FnOnce(serde_json::Value) -> Result<T, String>,
    {
        if !path.exists() {
            return MaintenanceHeartbeatReadResult {
                state: ReadState::Missing,
                path: path.to_string(),
                value: None,
                error: None,
            };
        }
        match self.json_store.load::<serde_json::Value>(path) {
            Ok(record) => match loader(record) {
                Ok(value) => {
                    if value.project_id() != self.project_id {
                        MaintenanceHeartbeatReadResult {
                            state: ReadState::Corrupt,
                            path: path.to_string(),
                            value: None,
                            error: Some("project_id mismatch".into()),
                        }
                    } else {
                        MaintenanceHeartbeatReadResult {
                            state: ReadState::Ok,
                            path: path.to_string(),
                            value: Some(value),
                            error: None,
                        }
                    }
                }
                Err(e) => MaintenanceHeartbeatReadResult {
                    state: ReadState::Corrupt,
                    path: path.to_string(),
                    value: None,
                    error: Some(e),
                },
            },
            Err(e) => MaintenanceHeartbeatReadResult {
                state: ReadState::Corrupt,
                path: path.to_string(),
                value: None,
                error: Some(e.to_string()),
            },
        }
    }

    fn ensure_project(&self, project_id: &str) -> Result<(), StorageError> {
        if project_id.trim() != self.project_id {
            return Err(StorageError::Corrupt("project_id mismatch".into()));
        }
        Ok(())
    }
}
