#![allow(clippy::useless_conversion)]

use camino::Utf8Path;
use ccb_heartbeat::{
    evaluate_heartbeat as evaluate_heartbeat_rs, HeartbeatAction as HeartbeatActionRs,
    HeartbeatDecision as HeartbeatDecisionRs, HeartbeatPolicy as HeartbeatPolicyRs,
    HeartbeatState as HeartbeatStateRs, HeartbeatStateStore as HeartbeatStateStoreRs,
};
use ccb_storage::paths::PathLayout;
use pyo3::prelude::*;
use pyo3::types::PyDict;

/// Python-exposed `HeartbeatAction` enum.
#[pyclass(name = "HeartbeatAction", eq, eq_int, frozen)]
#[derive(Clone, Copy, PartialEq)]
pub enum HeartbeatAction {
    Idle,
    Reset,
    Enter,
    Repeat,
}

impl From<HeartbeatActionRs> for HeartbeatAction {
    fn from(action: HeartbeatActionRs) -> Self {
        match action {
            HeartbeatActionRs::Idle => HeartbeatAction::Idle,
            HeartbeatActionRs::Reset => HeartbeatAction::Reset,
            HeartbeatActionRs::Enter => HeartbeatAction::Enter,
            HeartbeatActionRs::Repeat => HeartbeatAction::Repeat,
        }
    }
}

impl From<HeartbeatAction> for HeartbeatActionRs {
    fn from(action: HeartbeatAction) -> Self {
        match action {
            HeartbeatAction::Idle => HeartbeatActionRs::Idle,
            HeartbeatAction::Reset => HeartbeatActionRs::Reset,
            HeartbeatAction::Enter => HeartbeatActionRs::Enter,
            HeartbeatAction::Repeat => HeartbeatActionRs::Repeat,
        }
    }
}

/// Python-exposed `HeartbeatPolicy`.
#[pyclass(name = "HeartbeatPolicy", frozen)]
#[derive(Clone)]
pub struct HeartbeatPolicy {
    inner: HeartbeatPolicyRs,
}

#[pymethods]
impl HeartbeatPolicy {
    #[new]
    #[pyo3(signature = (silence_start_after_s, repeat_interval_s, max_notice_count=None))]
    fn new(
        silence_start_after_s: f64,
        repeat_interval_s: f64,
        max_notice_count: Option<u32>,
    ) -> PyResult<Self> {
        let inner =
            HeartbeatPolicyRs::new(silence_start_after_s, repeat_interval_s, max_notice_count)
                .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
        Ok(Self { inner })
    }

    #[getter]
    fn silence_start_after_s(&self) -> f64 {
        self.inner.silence_start_after_s
    }

    #[getter]
    fn repeat_interval_s(&self) -> f64 {
        self.inner.repeat_interval_s
    }

    #[getter]
    fn max_notice_count(&self) -> Option<u32> {
        self.inner.max_notice_count
    }
}

/// Python-exposed `HeartbeatState`.
#[pyclass(name = "HeartbeatState", frozen)]
#[derive(Clone)]
pub struct HeartbeatState {
    inner: HeartbeatStateRs,
}

#[pymethods]
impl HeartbeatState {
    #[new]
    #[pyo3(signature = (subject_kind, subject_id, owner, last_progress_at, last_notice_at=None, heartbeat_started_at=None, notice_count=0, updated_at=None))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        subject_kind: String,
        subject_id: String,
        owner: String,
        last_progress_at: String,
        last_notice_at: Option<String>,
        heartbeat_started_at: Option<String>,
        notice_count: u32,
        updated_at: Option<String>,
    ) -> PyResult<Self> {
        let updated_at = updated_at.unwrap_or_else(|| last_progress_at.clone());
        let inner = HeartbeatStateRs::new(
            subject_kind,
            subject_id,
            owner,
            last_progress_at,
            last_notice_at,
            heartbeat_started_at,
            notice_count,
            updated_at,
        )
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
        Ok(Self { inner })
    }

    #[getter]
    fn subject_kind(&self) -> &str {
        &self.inner.subject_kind
    }

    #[getter]
    fn subject_id(&self) -> &str {
        &self.inner.subject_id
    }

    #[getter]
    fn owner(&self) -> &str {
        &self.inner.owner
    }

    #[getter]
    fn last_progress_at(&self) -> &str {
        &self.inner.last_progress_at
    }

    #[getter]
    fn last_notice_at(&self) -> Option<&str> {
        self.inner.last_notice_at.as_deref()
    }

    #[getter]
    fn heartbeat_started_at(&self) -> Option<&str> {
        self.inner.heartbeat_started_at.as_deref()
    }

    #[getter]
    fn notice_count(&self) -> u32 {
        self.inner.notice_count
    }

    #[getter]
    fn updated_at(&self) -> &str {
        &self.inner.updated_at
    }

    fn to_record(&self) -> PyResult<Py<PyDict>> {
        Python::with_gil(|py| {
            let record = self.inner.to_record();
            let dict = serde_json_to_pydict(py, &record)?;
            Ok(dict)
        })
    }

    #[staticmethod]
    fn from_record(record: Bound<'_, PyDict>) -> PyResult<Self> {
        let value: serde_json::Value = pydict_to_serde_json(&record)?;
        let inner = HeartbeatStateRs::from_record(value)
            .map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
        Ok(Self { inner })
    }
}

/// Python-exposed `HeartbeatDecision`.
#[pyclass(name = "HeartbeatDecision", frozen)]
#[derive(Clone)]
pub struct HeartbeatDecision {
    inner: HeartbeatDecisionRs,
}

#[pymethods]
impl HeartbeatDecision {
    #[getter]
    fn action(&self) -> HeartbeatAction {
        self.inner.action.into()
    }

    #[getter]
    fn subject_kind(&self) -> &str {
        &self.inner.subject_kind
    }

    #[getter]
    fn subject_id(&self) -> &str {
        &self.inner.subject_id
    }

    #[getter]
    fn owner(&self) -> &str {
        &self.inner.owner
    }

    #[getter]
    fn last_progress_at(&self) -> &str {
        &self.inner.last_progress_at
    }

    #[getter]
    fn last_notice_at(&self) -> Option<&str> {
        self.inner.last_notice_at.as_deref()
    }

    #[getter]
    fn silence_seconds(&self) -> f64 {
        self.inner.silence_seconds
    }

    #[getter]
    fn notice_count(&self) -> u32 {
        self.inner.notice_count
    }

    #[getter]
    fn notice_due(&self) -> bool {
        self.inner.notice_due()
    }
}

/// Evaluate the heartbeat state machine.
#[pyfunction]
#[pyo3(signature = (policy, subject_kind, subject_id, owner, observed_last_progress_at, now, state=None))]
fn evaluate_heartbeat(
    policy: &HeartbeatPolicy,
    subject_kind: &str,
    subject_id: &str,
    owner: &str,
    observed_last_progress_at: &str,
    now: &str,
    state: Option<&HeartbeatState>,
) -> PyResult<(HeartbeatState, HeartbeatDecision)> {
    let state_ref = state.map(|s| &s.inner);
    let (next_state, decision) = evaluate_heartbeat_rs(
        &policy.inner,
        subject_kind,
        subject_id,
        owner,
        observed_last_progress_at,
        now,
        state_ref,
    );
    Ok((
        HeartbeatState { inner: next_state },
        HeartbeatDecision { inner: decision },
    ))
}

/// Python-exposed `HeartbeatStateStore`.
#[pyclass(name = "HeartbeatStateStore")]
pub struct HeartbeatStateStore {
    inner: HeartbeatStateStoreRs,
}

#[pymethods]
impl HeartbeatStateStore {
    #[new]
    #[pyo3(signature = (root_path))]
    fn new(root_path: &str) -> PyResult<Self> {
        let path = Utf8Path::new(root_path);
        let layout = PathLayout::new(path);
        let inner = HeartbeatStateStoreRs::new(layout);
        Ok(Self { inner })
    }

    fn load(&self, subject_kind: &str, subject_id: &str) -> PyResult<Option<HeartbeatState>> {
        self.inner
            .load(subject_kind, subject_id)
            .map(|opt| opt.map(|inner| HeartbeatState { inner }))
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyOSError, _>(e.to_string()))
    }

    fn save(&self, state: &HeartbeatState) -> PyResult<()> {
        self.inner
            .save(&state.inner)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyOSError, _>(e.to_string()))
    }

    fn remove(&self, subject_kind: &str, subject_id: &str) -> PyResult<()> {
        self.inner
            .remove(subject_kind, subject_id)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyOSError, _>(e.to_string()))
    }

    #[pyo3(signature = (subject_kind=None))]
    fn list_all(&self, subject_kind: Option<&str>) -> PyResult<Vec<HeartbeatState>> {
        self.inner
            .list_all(subject_kind)
            .map(|states| {
                states
                    .into_iter()
                    .map(|inner| HeartbeatState { inner })
                    .collect()
            })
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyOSError, _>(e.to_string()))
    }
}

/// Convert a `serde_json::Value` into a `PyDict`.
fn serde_json_to_pydict(py: Python, value: &serde_json::Value) -> PyResult<Py<PyDict>> {
    let json_str = serde_json::to_string(value).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("serialization failed: {e}"))
    })?;
    let module = py.import_bound("json")?;
    let loads = module.getattr("loads")?;
    let obj = loads.call1((json_str,))?;
    Ok(obj.downcast::<PyDict>()?.clone().unbind())
}

/// Convert a `PyDict` into a `serde_json::Value`.
fn pydict_to_serde_json(dict: &Bound<'_, PyDict>) -> PyResult<serde_json::Value> {
    let py = dict.py();
    let module = py.import_bound("json")?;
    let dumps = module.getattr("dumps")?;
    let json_str = dumps.call1((dict,))?.extract::<String>()?;
    serde_json::from_str(&json_str)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("invalid JSON: {e}")))
}

/// The `heartbeat` Python module.
#[pymodule]
fn ccb_py_heartbeat(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<HeartbeatAction>()?;
    m.add_class::<HeartbeatPolicy>()?;
    m.add_class::<HeartbeatState>()?;
    m.add_class::<HeartbeatDecision>()?;
    m.add_class::<HeartbeatStateStore>()?;
    m.add_function(wrap_pyfunction!(evaluate_heartbeat, m)?)?;
    m.add("SCHEMA_VERSION", ccb_heartbeat::SCHEMA_VERSION)?;
    Ok(())
}
