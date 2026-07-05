#![allow(clippy::useless_conversion)]

use camino::Utf8Path;
use ccb_jobs::models::{JobRecord, JobStatus, MessageEnvelope};
use ccb_mailbox::facade_recording::CompletionDecision;
use ccb_mailbox::kernel::Clock;
use ccb_mailbox::models::{
    CallbackEdgeState as MsgCallbackEdgeState, MessageState as MsgMessageState,
    ReplyTerminalStatus as MsgReplyTerminalStatus,
};
use ccb_mailbox::stores::CallbackEdgeChanges;
use ccb_mailbox::{InboundEventStatus, InboundEventType, MailboxKernelService, MailboxRecord};
use ccb_message_bureau::{MessageBureauControlService, MessageBureauFacade};
use ccb_storage::paths::PathLayout;
use pyo3::exceptions::{PyOSError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict, PyList};
use serde_json::Value;
use std::sync::Arc;

// ---------------------------------------------------------------------------
// JSON conversion helpers (serde_json::Value <-> Python object via json.loads)
// ---------------------------------------------------------------------------

fn serde_value_to_pyobject(py: Python<'_>, value: &Value) -> PyResult<Py<PyAny>> {
    let json_str = serde_json::to_string(value).map_err(|e| {
        PyErr::new::<PyRuntimeError, _>(format!("serialization failed: {e}"))
    })?;
    let module = py.import_bound("json")?;
    let loads = module.getattr("loads")?;
    let obj = loads.call1((json_str,))?;
    Ok(obj.unbind())
}

fn pyobject_to_serde_value(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
    let py = obj.py();
    let module = py.import_bound("json")?;
    let dumps = module.getattr("dumps")?;
    let json_str = dumps.call1((obj,))?.extract::<String>()?;
    serde_json::from_str(&json_str)
        .map_err(|e| PyErr::new::<PyValueError, _>(format!("invalid JSON: {e}")))
}

fn to_py_object<T: serde::Serialize>(py: Python<'_>, value: T) -> PyResult<Py<PyAny>> {
    let v = serde_json::to_value(value).map_err(|e| {
        PyErr::new::<PyRuntimeError, _>(format!("serialization failed: {e}"))
    })?;
    serde_value_to_pyobject(py, &v)
}

fn option_to_py_object<T: serde::Serialize>(
    py: Python<'_>,
    value: Option<T>,
) -> PyResult<Py<PyAny>> {
    match value {
        Some(v) => to_py_object(py, v),
        None => Ok(py.None()),
    }
}

// ---------------------------------------------------------------------------
// Error conversion
// ---------------------------------------------------------------------------

fn mailbox_error_to_pyerr(e: ccb_mailbox::MailboxError) -> PyErr {
    match e {
        ccb_mailbox::MailboxError::Storage(_) | ccb_mailbox::MailboxError::Io(_) => {
            PyErr::new::<PyOSError, _>(e.to_string())
        }
        ccb_mailbox::MailboxError::Json(_) | ccb_mailbox::MailboxError::RecordCodec(_) => {
            PyErr::new::<PyValueError, _>(e.to_string())
        }
        ccb_mailbox::MailboxError::NotFound(_) => PyErr::new::<PyRuntimeError, _>(e.to_string()),
    }
}

/// Run a closure that may panic (the underlying Rust control queue uses panics
/// for validation errors) and convert the panic message into a Python
/// ``ValueError`` so callers see the same exception type as the Python impl.
fn catch_panic_as_value_error<T>(f: impl FnOnce() -> T) -> PyResult<T> {
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(f)) {
        Ok(value) => Ok(value),
        Err(payload) => {
            let msg = if let Some(s) = payload.downcast_ref::<String>() {
                s.clone()
            } else if let Some(s) = payload.downcast_ref::<&str>() {
                (*s).to_string()
            } else {
                "panic in Rust message bureau".to_string()
            };
            Err(PyErr::new::<PyValueError, _>(msg))
        }
    }
}

// ---------------------------------------------------------------------------
// mailbox_kernel submodule
// ---------------------------------------------------------------------------

#[pyclass(name = "InboundEventType", eq, eq_int, frozen)]
#[derive(Clone, Copy, PartialEq)]
pub enum PyInboundEventType {
    TaskRequest,
    TaskReply,
    CompletionNotice,
    RetrySignal,
    SystemSignal,
    BarrierRelease,
}

fn inbound_event_type_from_int(value: u32) -> PyResult<InboundEventType> {
    match value {
        0 => Ok(InboundEventType::TaskRequest),
        1 => Ok(InboundEventType::TaskReply),
        2 => Ok(InboundEventType::CompletionNotice),
        3 => Ok(InboundEventType::RetrySignal),
        4 => Ok(InboundEventType::SystemSignal),
        5 => Ok(InboundEventType::BarrierRelease),
        _ => Err(PyErr::new::<PyValueError, _>(format!(
            "invalid InboundEventType discriminant: {value}"
        ))),
    }
}

fn option_inbound_event_type_from_int(
    value: Option<u32>,
) -> PyResult<Option<InboundEventType>> {
    match value {
        None => Ok(None),
        Some(v) => Ok(Some(inbound_event_type_from_int(v)?)),
    }
}

#[pyclass(name = "InboundEventStatus", eq, eq_int, frozen)]
#[derive(Clone, Copy, PartialEq)]
pub enum PyInboundEventStatus {
    Created,
    Queued,
    Delivering,
    Consumed,
    Superseded,
    Abandoned,
}

fn inbound_event_status_from_int(value: u32) -> PyResult<InboundEventStatus> {
    match value {
        0 => Ok(InboundEventStatus::Created),
        1 => Ok(InboundEventStatus::Queued),
        2 => Ok(InboundEventStatus::Delivering),
        3 => Ok(InboundEventStatus::Consumed),
        4 => Ok(InboundEventStatus::Superseded),
        5 => Ok(InboundEventStatus::Abandoned),
        _ => Err(PyErr::new::<PyValueError, _>(format!(
            "invalid InboundEventStatus discriminant: {value}"
        ))),
    }
}

#[pyclass(name = "MailboxState", eq, eq_int, frozen)]
#[derive(Clone, Copy, PartialEq)]
pub enum PyMailboxState {
    Idle,
    Delivering,
    Blocked,
    Recovering,
    Degraded,
}

#[pyclass(name = "LeaseState", eq, eq_int, frozen)]
#[derive(Clone, Copy, PartialEq)]
pub enum PyLeaseState {
    Acquired,
    Released,
    Expired,
    Orphaned,
}

#[pyclass(name = "MailboxKernelService")]
pub struct PyMailboxKernelService {
    inner: MailboxKernelService,
}

#[pymethods]
impl PyMailboxKernelService {
    #[new]
    fn new(root_path: &str) -> PyResult<Self> {
        let path = Utf8Path::new(root_path);
        let layout = PathLayout::new(path);
        Ok(Self {
            inner: MailboxKernelService::new(layout),
        })
    }

    fn latest_events(&self, py: Python<'_>, agent_name: &str) -> PyResult<Py<PyAny>> {
        let records = self.inner.latest_events(agent_name);
        to_py_object(py, records)
    }

    #[pyo3(signature = (agent_name, event_type=None))]
    fn pending_events(
        &self,
        py: Python<'_>,
        agent_name: &str,
        event_type: Option<u32>,
    ) -> PyResult<Py<PyAny>> {
        let event_type = option_inbound_event_type_from_int(event_type)?;
        let records = self.inner.pending_events(agent_name, event_type);
        to_py_object(py, records)
    }

    fn head_pending_event(&self, py: Python<'_>, agent_name: &str) -> PyResult<Py<PyAny>> {
        let record = self.inner.head_pending_event(agent_name);
        option_to_py_object(py, record)
    }

    #[pyo3(signature = (agent_name, event_type=None))]
    fn peek_next(
        &self,
        py: Python<'_>,
        agent_name: &str,
        event_type: Option<u32>,
    ) -> PyResult<Py<PyAny>> {
        let event_type = option_inbound_event_type_from_int(event_type)?;
        let record = self.inner.peek_next(agent_name, event_type);
        option_to_py_object(py, record)
    }

    #[pyo3(signature = (agent_name, inbound_event_id, started_at=None))]
    fn claim(
        &self,
        py: Python<'_>,
        agent_name: &str,
        inbound_event_id: &str,
        started_at: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        let record = self.inner.claim(agent_name, inbound_event_id, started_at);
        option_to_py_object(py, record)
    }

    #[pyo3(signature = (agent_name, event_type=None, started_at=None))]
    fn claim_next(
        &self,
        py: Python<'_>,
        agent_name: &str,
        event_type: Option<u32>,
        started_at: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        let event_type = option_inbound_event_type_from_int(event_type)?;
        let record = self.inner.claim_next(agent_name, event_type, started_at);
        option_to_py_object(py, record)
    }

    #[pyo3(signature = (agent_name, inbound_event_id, finished_at=None))]
    fn consume(
        &self,
        py: Python<'_>,
        agent_name: &str,
        inbound_event_id: &str,
        finished_at: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        let record = self.inner.consume(agent_name, inbound_event_id, finished_at);
        option_to_py_object(py, record)
    }

    #[pyo3(signature = (agent_name, inbound_event_id, finished_at=None))]
    fn abandon(
        &self,
        py: Python<'_>,
        agent_name: &str,
        inbound_event_id: &str,
        finished_at: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        let record = self.inner.abandon(agent_name, inbound_event_id, finished_at);
        option_to_py_object(py, record)
    }

    #[pyo3(signature = (agent_name, inbound_event_id, finished_at=None))]
    fn supersede(
        &self,
        py: Python<'_>,
        agent_name: &str,
        inbound_event_id: &str,
        finished_at: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        let record = self.inner.supersede(agent_name, inbound_event_id, finished_at);
        option_to_py_object(py, record)
    }

    #[pyo3(signature = (agent_name, inbound_event_id, started_at=None, finished_at=None))]
    #[allow(clippy::too_many_arguments)]
    fn ack_reply(
        &self,
        py: Python<'_>,
        agent_name: &str,
        inbound_event_id: &str,
        started_at: Option<&str>,
        finished_at: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        let record = self.inner.ack_reply(agent_name, inbound_event_id, started_at, finished_at);
        option_to_py_object(py, record)
    }

    #[pyo3(signature = (agent_name, inbound_event_id, status, finished_at=None))]
    fn mark_terminal(
        &self,
        py: Python<'_>,
        agent_name: &str,
        inbound_event_id: &str,
        status: u32,
        finished_at: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        let status = inbound_event_status_from_int(status)?;
        let record = self.inner.mark_terminal(agent_name, inbound_event_id, status, finished_at);
        option_to_py_object(py, record)
    }

    #[pyo3(signature = (agent_name, updated_at=None))]
    fn rebuild_mailbox_summary(
        &self,
        py: Python<'_>,
        agent_name: &str,
        updated_at: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        let record = self
            .inner
            .rebuild_mailbox_summary(agent_name, updated_at)
            .map_err(mailbox_error_to_pyerr)?;
        to_py_object(py, record)
    }

    #[pyo3(signature = (agent_name, updated_at=None, prior=None, summary_source="projection"))]
    fn project_mailbox_summary(
        &self,
        py: Python<'_>,
        agent_name: &str,
        updated_at: Option<&str>,
        prior: Option<&Bound<'_, PyDict>>,
        summary_source: &str,
    ) -> PyResult<Py<PyAny>> {
        let prior_record: Option<MailboxRecord> = match prior {
            None => None,
            Some(dict) => {
                let value = pyobject_to_serde_value(dict.as_any())?;
                Some(serde_json::from_value(value).map_err(|e| {
                    PyErr::new::<PyValueError, _>(format!("invalid prior summary: {e}"))
                })?)
            }
        };
        let record = self
            .inner
            .project_mailbox_summary(agent_name, updated_at, prior_record.as_ref(), summary_source)
            .map_err(mailbox_error_to_pyerr)?;
        to_py_object(py, record)
    }

    #[pyo3(signature = (agent_name, updated_at=None))]
    fn refresh_mailbox(
        &self,
        py: Python<'_>,
        agent_name: &str,
        updated_at: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        let record = self
            .inner
            .refresh_mailbox(agent_name, updated_at)
            .map_err(mailbox_error_to_pyerr)?;
        to_py_object(py, record)
    }

    #[pyo3(signature = (
        agent_name,
        queue_delta=0,
        pending_reply_delta=0,
        active_inbound_event_id=None,
        last_started_at=None,
        last_finished_at=None,
        updated_at=None
    ))]
    #[allow(clippy::too_many_arguments)]
    fn apply_incremental_summary_update(
        &self,
        py: Python<'_>,
        agent_name: &str,
        queue_delta: i32,
        pending_reply_delta: i32,
        active_inbound_event_id: Option<&Bound<'_, PyAny>>,
        last_started_at: Option<&str>,
        last_finished_at: Option<&str>,
        updated_at: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        let active = map_optional_event_id(active_inbound_event_id)?;
        let record = self
            .inner
            .apply_incremental_summary_update(
                agent_name,
                queue_delta,
                pending_reply_delta,
                active,
                last_started_at,
                last_finished_at,
                updated_at,
            )
            .map_err(mailbox_error_to_pyerr)?;
        to_py_object(py, record)
    }

    #[pyo3(signature = (
        agent_name,
        queue_delta=0,
        pending_reply_delta=0,
        active_inbound_event_id=None,
        last_started_at=None,
        last_finished_at=None,
        updated_at=None
    ))]
    #[allow(clippy::too_many_arguments)]
    fn upsert_mailbox_summary(
        &self,
        py: Python<'_>,
        agent_name: &str,
        queue_delta: i32,
        pending_reply_delta: i32,
        active_inbound_event_id: Option<&Bound<'_, PyAny>>,
        last_started_at: Option<&str>,
        last_finished_at: Option<&str>,
        updated_at: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        self.apply_incremental_summary_update(
            py,
            agent_name,
            queue_delta,
            pending_reply_delta,
            active_inbound_event_id,
            last_started_at,
            last_finished_at,
            updated_at,
        )
    }

    #[pyo3(signature = (
        agent_name,
        inbound_event_id,
        payload_ref=None,
        status=0,
        updated_at=None,
        clear_progress=false
    ))]
    #[allow(clippy::too_many_arguments)]
    fn rewrite_head(
        &self,
        py: Python<'_>,
        agent_name: &str,
        inbound_event_id: &str,
        payload_ref: Option<&str>,
        status: u32,
        updated_at: Option<&str>,
        clear_progress: bool,
    ) -> PyResult<Py<PyAny>> {
        let status = inbound_event_status_from_int(status)?;
        let record = self.inner.rewrite_head(
            agent_name,
            inbound_event_id,
            payload_ref,
            status,
            updated_at,
            clear_progress,
        );
        option_to_py_object(py, record)
    }
}

/// Map the Python `active_inbound_event_id` argument to the Rust kernel's
/// `Option<Option<String>>` shape:
/// - absent or `Ellipsis` -> `None` (keep prior)
/// - `None` -> `Some(None)` (clear active id)
/// - string -> `Some(Some(id))`
fn map_optional_event_id(
    value: Option<&Bound<'_, PyAny>>,
) -> PyResult<Option<Option<String>>> {
    let Some(obj) = value else {
        return Ok(None);
    };
    if obj.is_ellipsis() {
        return Ok(None);
    }
    if obj.is_none() {
        return Ok(Some(None));
    }
    Ok(Some(Some(obj.extract::<String>()?)))
}

// ---------------------------------------------------------------------------
// message_bureau submodule
// ---------------------------------------------------------------------------

#[pyclass(name = "MessageState", eq, eq_int, frozen)]
#[derive(Clone, Copy, PartialEq)]
pub enum PyMessageState {
    Created,
    Queued,
    Dispatching,
    Running,
    PartiallyReplied,
    Completed,
    Incomplete,
    Failed,
    Cancelled,
    DeadLetter,
}

#[pyclass(name = "AttemptState", eq, eq_int, frozen)]
#[derive(Clone, Copy, PartialEq)]
pub enum PyAttemptState {
    Pending,
    Delivering,
    Running,
    WaitingCompletion,
    ReplyReady,
    Stalled,
    RuntimeDead,
    Failed,
    Incomplete,
    Cancelled,
    Superseded,
    DeadLetter,
    Completed,
}

#[pyclass(name = "ReplyTerminalStatus", eq, eq_int, frozen)]
#[derive(Clone, Copy, PartialEq)]
pub enum PyReplyTerminalStatus {
    Completed,
    Incomplete,
    Failed,
    Cancelled,
}

#[pyclass(name = "CallbackEdgeState", eq, eq_int, frozen)]
#[derive(Clone, Copy, PartialEq)]
pub enum PyCallbackEdgeState {
    Pending,
    ChildCompleted,
    ContinuationSubmitted,
    Done,
    Failed,
    TimedOut,
}

fn default_clock() -> Clock {
    Arc::new(|| {
        chrono::Utc::now()
            .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
            .replace("+00:00", "Z")
    })
}

fn optional_config_value(config: Option<&Bound<'_, PyAny>>) -> PyResult<Option<Value>> {
    match config {
        None => Ok(None),
        Some(obj) => {
            let value = pyobject_to_serde_value(obj)?;
            Ok(Some(value))
        }
    }
}

// ---------------------------------------------------------------------------
// Message bureau argument parsing helpers
// ---------------------------------------------------------------------------

/// Extract a string value from a Python enum (`str, Enum`) or plain string.
fn enum_value_string(value: &Bound<'_, PyAny>) -> PyResult<String> {
    if let Ok(s) = value.extract::<String>() {
        return Ok(s);
    }
    let v = value.getattr("value")?;
    v.extract::<String>()
}

/// Parse a Python enum/string into a Rust enum that deserializes from snake_case.
fn parse_enum<T: for<'de> serde::Deserialize<'de>>(value: &Bound<'_, PyAny>) -> PyResult<T> {
    let s = enum_value_string(value)?;
    serde_json::from_value(Value::String(s))
        .map_err(|e| PyErr::new::<PyValueError, _>(format!("invalid enum value: {e}")))
}

fn parse_job_record(job: &Bound<'_, PyAny>) -> PyResult<JobRecord> {
    let value = pyobject_to_serde_value(job)?;
    serde_json::from_value(value)
        .map_err(|e| PyErr::new::<PyValueError, _>(format!("invalid job record: {e}")))
}

fn parse_completion_decision(decision: &Bound<'_, PyAny>) -> PyResult<CompletionDecision> {
    let value = pyobject_to_serde_value(decision)?;

    let terminal = value
        .get("terminal")
        .and_then(|v| v.as_bool())
        .ok_or_else(|| PyErr::new::<PyValueError, _>("decision.terminal is required"))?;

    let status_str = value
        .get("status")
        .and_then(|v| v.as_str())
        .ok_or_else(|| PyErr::new::<PyValueError, _>("decision.status is required"))?;
    let status: JobStatus = serde_json::from_value(Value::String(status_str.to_string()))
        .map_err(|e| PyErr::new::<PyValueError, _>(format!("invalid decision.status: {e}")))?;

    let reason = value
        .get("reason")
        .and_then(|v| v.as_str().map(|s| s.to_string()));

    let reply = value
        .get("reply")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| PyErr::new::<PyValueError, _>("decision.reply is required"))?;

    let provider_turn_ref = value
        .get("provider_turn_ref")
        .and_then(|v| v.as_str().map(|s| s.to_string()));

    let diagnostics = value
        .get("diagnostics")
        .cloned()
        .unwrap_or_else(|| Value::Object(Default::default()));

    Ok(CompletionDecision {
        terminal,
        status,
        reason,
        reply,
        provider_turn_ref,
        diagnostics,
    })
}

fn parse_callback_edge_record(edge: &Bound<'_, PyAny>) -> PyResult<ccb_mailbox::models::CallbackEdgeRecord> {
    let value = pyobject_to_serde_value(edge)?;
    serde_json::from_value(value)
        .map_err(|e| PyErr::new::<PyValueError, _>(format!("invalid callback edge: {e}")))
}

fn parse_callback_edge_changes(
    kwargs: Option<&Bound<'_, PyDict>>,
) -> PyResult<CallbackEdgeChanges> {
    let mut changes = CallbackEdgeChanges::new();
    let Some(kwargs) = kwargs else {
        return Ok(changes);
    };

    if let Some(value) = kwargs.get_item("state")? {
        changes.state = Some(parse_enum::<MsgCallbackEdgeState>(&value)?);
    }
    if let Some(value) = kwargs.get_item("child_reply_id")? {
        changes.child_reply_id = Some(value.extract::<String>()?);
    }
    if let Some(value) = kwargs.get_item("child_status")? {
        changes.child_status = Some(value.extract::<String>()?);
    }
    if let Some(value) = kwargs.get_item("continuation_job_id")? {
        changes.continuation_job_id = Some(value.extract::<String>()?);
    }
    if let Some(value) = kwargs.get_item("continuation_message_id")? {
        changes.continuation_message_id = Some(value.extract::<String>()?);
    }
    if let Some(value) = kwargs.get_item("timeout_at")? {
        changes.timeout_at = if value.is_none() {
            Some(None)
        } else {
            Some(Some(value.extract::<String>()?))
        };
    }
    if let Some(value) = kwargs.get_item("diagnostics")? {
        changes.diagnostics = Some(pyobject_to_serde_value(&value)?);
    }
    if let Some(value) = kwargs.get_item("updated_at")? {
        changes.updated_at = Some(value.extract::<String>()?);
    }

    Ok(changes)
}

#[pyclass(name = "MessageBureauFacade")]
pub struct PyMessageBureauFacade {
    inner: MessageBureauFacade,
}

#[pymethods]
impl PyMessageBureauFacade {
    #[new]
    #[pyo3(signature = (root_path, config=None))]
    fn new(root_path: &str, config: Option<&Bound<'_, PyAny>>) -> PyResult<Self> {
        let path = Utf8Path::new(root_path);
        let layout = PathLayout::new(path);
        let config = optional_config_value(config)?;
        Ok(Self {
            inner: MessageBureauFacade::new(layout, config, default_clock()),
        })
    }

    #[pyo3(signature = (request, jobs, accepted_at, submission_id=None, origin_message_id=None))]
    fn record_submission(
        &self,
        request: &Bound<'_, PyAny>,
        jobs: &Bound<'_, PyAny>,
        accepted_at: &str,
        submission_id: Option<&str>,
        origin_message_id: Option<&str>,
    ) -> PyResult<Option<String>> {
        let request_value = pyobject_to_serde_value(request)?;
        let request_envelope: MessageEnvelope = serde_json::from_value(request_value).map_err(
            |e| PyErr::new::<PyValueError, _>(format!("invalid request envelope: {e}")),
        )?;

        let jobs_list = jobs.downcast::<PyList>().map_err(|_| {
            PyErr::new::<PyValueError, _>("jobs must be a list of dicts".to_string())
        })?;
        let mut job_records = Vec::with_capacity(jobs_list.len());
        for item in jobs_list.iter() {
            let value = pyobject_to_serde_value(&item)?;
            let job: JobRecord = serde_json::from_value(value).map_err(|e| {
                PyErr::new::<PyValueError, _>(format!("invalid job record: {e}"))
            })?;
            job_records.push(job);
        }

        Ok(self.inner.record_submission(
            &request_envelope,
            &job_records,
            submission_id,
            accepted_at,
            origin_message_id,
        ))
    }

    fn claimable_request_job_ids(&self, agent_name: &str) -> Vec<String> {
        self.inner.claimable_request_job_ids(agent_name)
    }

    fn get_message(&self, py: Python<'_>, message_id: &str) -> PyResult<Py<PyAny>> {
        let record = self.inner.get_message(message_id);
        option_to_py_object(py, record)
    }

    fn all_messages(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let records = self.inner.all_messages();
        to_py_object(py, records)
    }

    #[pyo3(signature = (job, started_at))]
    fn mark_attempt_started(
        &self,
        job: &Bound<'_, PyAny>,
        started_at: &str,
    ) -> PyResult<()> {
        let job = parse_job_record(job)?;
        self.inner.mark_attempt_started(&job, started_at);
        Ok(())
    }

    #[pyo3(signature = (job, decision, finished_at))]
    fn record_attempt_terminal(
        &self,
        job: &Bound<'_, PyAny>,
        decision: &Bound<'_, PyAny>,
        finished_at: &str,
    ) -> PyResult<()> {
        let job = parse_job_record(job)?;
        let decision = parse_completion_decision(decision)?;
        self.inner.record_attempt_terminal(&job, &decision, finished_at);
        Ok(())
    }

    #[pyo3(signature = (job, decision, finished_at, deliver_to_caller=true))]
    fn record_reply(
        &self,
        job: &Bound<'_, PyAny>,
        decision: &Bound<'_, PyAny>,
        finished_at: &str,
        deliver_to_caller: bool,
    ) -> PyResult<Option<String>> {
        let job = parse_job_record(job)?;
        let decision = parse_completion_decision(decision)?;
        Ok(self.inner.record_reply(&job, &decision, finished_at, deliver_to_caller))
    }

    #[pyo3(signature = (job, reply, finished_at, diagnostics=None, terminal_status=None, deliver_to_actor=None))]
    fn record_notice(
        &self,
        job: &Bound<'_, PyAny>,
        reply: &str,
        finished_at: &str,
        diagnostics: Option<&Bound<'_, PyAny>>,
        terminal_status: Option<&Bound<'_, PyAny>>,
        deliver_to_actor: Option<&str>,
    ) -> PyResult<Option<String>> {
        let job = parse_job_record(job)?;
        let diagnostics = diagnostics.map(pyobject_to_serde_value).transpose()?;
        let terminal_status = match terminal_status {
            None => MsgReplyTerminalStatus::Incomplete,
            Some(value) => parse_enum::<MsgReplyTerminalStatus>(value)?,
        };
        Ok(self.inner.record_notice(
            &job,
            reply,
            diagnostics,
            finished_at,
            terminal_status,
            deliver_to_actor,
        ))
    }

    #[pyo3(signature = (job, decision, finished_at, deliver_to_caller=true, record_reply=true))]
    fn record_terminal(
        &self,
        job: &Bound<'_, PyAny>,
        decision: &Bound<'_, PyAny>,
        finished_at: &str,
        deliver_to_caller: bool,
        record_reply: bool,
    ) -> PyResult<Option<String>> {
        let job = parse_job_record(job)?;
        let decision = parse_completion_decision(decision)?;
        Ok(self.inner.record_terminal(
            &job,
            &decision,
            finished_at,
            deliver_to_caller,
            record_reply,
        ))
    }

    #[pyo3(signature = (message_id, job, accepted_at))]
    fn record_retry_attempt(
        &self,
        message_id: &str,
        job: &Bound<'_, PyAny>,
        accepted_at: &str,
    ) -> PyResult<String> {
        let job = parse_job_record(job)?;
        self.inner
            .record_retry_attempt(message_id, &job, accepted_at)
            .map_err(mailbox_error_to_pyerr)
    }

    #[pyo3(signature = (message_id, next_state, updated_at))]
    fn set_message_state(
        &self,
        message_id: &str,
        next_state: &Bound<'_, PyAny>,
        updated_at: &str,
    ) -> PyResult<()> {
        let next_state = parse_enum::<MsgMessageState>(next_state)?;
        self.inner.set_message_state(message_id, next_state, updated_at);
        Ok(())
    }

    #[pyo3(signature = (edge))]
    fn record_callback_edge(&self, edge: &Bound<'_, PyAny>) -> PyResult<()> {
        let edge = parse_callback_edge_record(edge)?;
        self.inner.record_callback_edge(&edge).map_err(mailbox_error_to_pyerr)
    }

    fn callback_edge_for_child_job(
        &self,
        py: Python<'_>,
        child_job_id: &str,
    ) -> PyResult<Py<PyAny>> {
        option_to_py_object(py, self.inner.callback_edge_for_child_job(child_job_id))
    }

    fn callback_edge_for_child_message(
        &self,
        py: Python<'_>,
        child_message_id: &str,
    ) -> PyResult<Py<PyAny>> {
        option_to_py_object(py, self.inner.callback_edge_for_child_message(child_message_id))
    }

    fn callback_edge_for_parent_job(
        &self,
        py: Python<'_>,
        parent_job_id: &str,
    ) -> PyResult<Py<PyAny>> {
        option_to_py_object(py, self.inner.callback_edge_for_parent_job(parent_job_id))
    }

    #[pyo3(signature = (edge, kwargs=None))]
    fn update_callback_edge(
        &self,
        py: Python<'_>,
        edge: &Bound<'_, PyAny>,
        kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        let edge = parse_callback_edge_record(edge)?;
        let changes = parse_callback_edge_changes(kwargs)?;
        let updated = self.inner.update_callback_edge(&edge, changes);
        to_py_object(py, updated)
    }

    fn callback_edge(&self, py: Python<'_>, edge_id: &str) -> PyResult<Py<PyAny>> {
        option_to_py_object(py, self.inner.callback_edge(edge_id))
    }

    fn pending_callback_edges(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        to_py_object(py, self.inner.pending_callback_edges())
    }
}

#[pyclass(name = "MessageBureauControlService")]
pub struct PyMessageBureauControlService {
    inner: MessageBureauControlService,
}

#[pymethods]
impl PyMessageBureauControlService {
    #[new]
    #[pyo3(signature = (root_path, config=None))]
    fn new(root_path: &str, config: Option<&Bound<'_, PyAny>>) -> PyResult<Self> {
        let path = Utf8Path::new(root_path);
        let layout = PathLayout::new(path);
        let config = optional_config_value(config)?;
        Ok(Self {
            inner: MessageBureauControlService::new(layout, config, None),
        })
    }

    #[pyo3(signature = (target, detail=None))]
    fn queue_summary(
        &self,
        py: Python<'_>,
        target: &str,
        detail: Option<bool>,
    ) -> PyResult<Py<PyAny>> {
        let value = catch_panic_as_value_error(|| self.inner.queue_summary(target, detail))?;
        serde_value_to_pyobject(py, &value)
    }

    fn agent_queue(&self, py: Python<'_>, agent_name: &str) -> PyResult<Py<PyAny>> {
        let value = self.inner.agent_queue(agent_name);
        serde_value_to_pyobject(py, &value)
    }

    fn trace(&self, py: Python<'_>, target: &str) -> PyResult<Py<PyAny>> {
        let value = self.inner.trace(target);
        serde_value_to_pyobject(py, &value)
    }

    #[pyo3(signature = (agent_name, detail=None))]
    fn inbox(
        &self,
        py: Python<'_>,
        agent_name: &str,
        detail: Option<bool>,
    ) -> PyResult<Py<PyAny>> {
        let value = self.inner.inbox(agent_name, detail);
        serde_value_to_pyobject(py, &value)
    }

    fn mailbox_head(&self, py: Python<'_>, agent_name: &str) -> PyResult<Py<PyAny>> {
        let value = self.inner.mailbox_head(agent_name);
        serde_value_to_pyobject(py, &value)
    }

    #[pyo3(signature = (agent_name, inbound_event_id=None))]
    fn ack_reply(
        &self,
        py: Python<'_>,
        agent_name: &str,
        inbound_event_id: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        let value = catch_panic_as_value_error(|| self.inner.ack_reply(agent_name, inbound_event_id))?;
        serde_value_to_pyobject(py, &value)
    }
}

// ---------------------------------------------------------------------------
// Module entrypoint
// ---------------------------------------------------------------------------

#[pymodule]
fn ccb_py_mailbox(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let py = m.py();

    // Mark the extension module as a package so `import ccb_py_mailbox.X`
    // resolves to the submodules registered below.
    m.add("__path__", vec!["".to_string()])?;

    let mailbox_kernel = PyModule::new_bound(py, "mailbox_kernel")?;
    mailbox_kernel.add_class::<PyInboundEventType>()?;
    mailbox_kernel.add_class::<PyInboundEventStatus>()?;
    mailbox_kernel.add_class::<PyMailboxState>()?;
    mailbox_kernel.add_class::<PyLeaseState>()?;
    mailbox_kernel.add_class::<PyMailboxKernelService>()?;
    mailbox_kernel.add("SCHEMA_VERSION", ccb_mailbox::SCHEMA_VERSION)?;
    m.add_submodule(&mailbox_kernel)?;

    let message_bureau = PyModule::new_bound(py, "message_bureau")?;
    message_bureau.add_class::<PyMessageState>()?;
    message_bureau.add_class::<PyAttemptState>()?;
    message_bureau.add_class::<PyReplyTerminalStatus>()?;
    message_bureau.add_class::<PyCallbackEdgeState>()?;
    message_bureau.add_class::<PyMessageBureauFacade>()?;
    message_bureau.add_class::<PyMessageBureauControlService>()?;
    message_bureau.add("SCHEMA_VERSION", ccb_message_bureau::SCHEMA_VERSION)?;
    m.add_submodule(&message_bureau)?;

    // Register the submodules in sys.modules so dotted imports work.
    let sys = py.import_bound("sys")?;
    let modules = sys.getattr("modules")?;
    modules.set_item("ccb_py_mailbox.mailbox_kernel", &mailbox_kernel)?;
    modules.set_item("ccb_py_mailbox.message_bureau", &message_bureau)?;

    Ok(())
}
