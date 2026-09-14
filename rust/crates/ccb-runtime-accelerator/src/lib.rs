use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs::File;
use std::hash::Hasher;
use std::io::{BufRead, BufReader, Seek, SeekFrom, Write};
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::process::Command;

#[cfg(unix)]
use std::os::unix::net::{UnixListener, UnixStream};

pub const SCHEMA_VERSION: u32 = 1;
const REQ_ID_PREFIX: &str = "CCB_REQ_ID:";
const DONE_PREFIX: &str = "CCB_DONE:";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AcceleratorRequest {
    pub method: String,
    #[serde(default)]
    pub params: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AcceleratorResponse {
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl AcceleratorResponse {
    pub fn ok(result: Value) -> Self {
        Self {
            ok: true,
            result: Some(result),
            error: None,
        }
    }

    pub fn err(message: impl Into<String>) -> Self {
        Self {
            ok: false,
            result: None,
            error: Some(message.into()),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProcessSample {
    pub pid: u32,
    pub ppid: u32,
    pub cpu_percent: f64,
    pub rss_kb: u64,
    pub command: String,
    pub args: String,
    pub kind: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct BaselineSnapshot {
    pub schema_version: u32,
    pub project_root: String,
    pub process_count: usize,
    pub processes: Vec<ProcessSample>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CodexObserveParams {
    #[serde(default)]
    pub jobs: Vec<CodexObserveJob>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CodexObserveJob {
    pub job_id: String,
    pub session_path: String,
    #[serde(default)]
    pub request_anchor: String,
    #[serde(default)]
    pub state: CodexObservationState,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CodexObservationState {
    #[serde(default)]
    pub offset: u64,
    #[serde(default = "default_next_seq")]
    pub next_seq: u64,
    #[serde(default)]
    pub anchor_seen: bool,
    #[serde(default)]
    pub bound_turn_id: String,
    #[serde(default)]
    pub bound_task_id: String,
    #[serde(default)]
    pub reply_buffer: String,
    #[serde(default)]
    pub last_agent_message: String,
    #[serde(default)]
    pub last_final_answer: String,
    #[serde(default)]
    pub last_assistant_message: String,
    #[serde(default)]
    pub last_assistant_signature: String,
    #[serde(default)]
    pub session_path: String,
}

impl Default for CodexObservationState {
    fn default() -> Self {
        Self {
            offset: 0,
            next_seq: default_next_seq(),
            anchor_seen: false,
            bound_turn_id: String::new(),
            bound_task_id: String::new(),
            reply_buffer: String::new(),
            last_agent_message: String::new(),
            last_final_answer: String::new(),
            last_assistant_message: String::new(),
            last_assistant_signature: String::new(),
            session_path: String::new(),
        }
    }
}

fn default_next_seq() -> u64 {
    1
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CodexCompletionItem {
    pub job_id: String,
    pub kind: String,
    pub seq: u64,
    pub payload: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CodexJobObservation {
    pub job_id: String,
    pub session_path: String,
    pub state: CodexObservationState,
    pub items: Vec<CodexCompletionItem>,
    pub reached_terminal: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CodexObserveResponse {
    pub schema_version: u32,
    pub observations: Vec<CodexJobObservation>,
}

pub fn handle_request_line(raw: &str) -> AcceleratorResponse {
    let request = match serde_json::from_str::<AcceleratorRequest>(raw.trim()) {
        Ok(request) => request,
        Err(err) => return AcceleratorResponse::err(format!("invalid request: {err}")),
    };
    handle_request(request)
}

pub fn handle_request(request: AcceleratorRequest) -> AcceleratorResponse {
    match request.method.as_str() {
        "ping" => AcceleratorResponse::ok(json!({
            "schema_version": SCHEMA_VERSION,
            "service": "ccb-runtime-accelerator",
            "status": "ok",
        })),
        "capabilities" => AcceleratorResponse::ok(json!({
            "schema_version": SCHEMA_VERSION,
            "capabilities": ["ping", "capabilities", "baseline_snapshot", "codex_observe"],
            "hot_loop_replacement_active": true,
        })),
        "baseline_snapshot" => {
            let project_root = request
                .params
                .get("project_root")
                .and_then(Value::as_str)
                .unwrap_or("");
            match baseline_snapshot(project_root) {
                Ok(snapshot) => match serde_json::to_value(snapshot) {
                    Ok(value) => AcceleratorResponse::ok(value),
                    Err(err) => AcceleratorResponse::err(format!("serialize snapshot: {err}")),
                },
                Err(err) => AcceleratorResponse::err(err.to_string()),
            }
        }
        "codex_observe" => match codex_observe(&request.params) {
            Ok(response) => match serde_json::to_value(response) {
                Ok(value) => AcceleratorResponse::ok(value),
                Err(err) => AcceleratorResponse::err(format!("serialize codex observation: {err}")),
            },
            Err(err) => AcceleratorResponse::err(err.to_string()),
        },
        other => AcceleratorResponse::err(format!("unknown method: {other}")),
    }
}

pub fn response_line(response: &AcceleratorResponse) -> String {
    serde_json::to_string(response)
        .unwrap_or_else(|_| r#"{"ok":false,"error":"serialize response failed"}"#.to_string())
        + "\n"
}

pub fn serve(socket_path: &Path) -> anyhow::Result<()> {
    serve_with_shutdown(socket_path, || false)
}

#[cfg(unix)]
pub fn serve_with_shutdown<F>(socket_path: &Path, should_shutdown: F) -> anyhow::Result<()>
where
    F: Fn() -> bool,
{
    if let Some(parent) = socket_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    if socket_path.exists() {
        let _ = std::fs::remove_file(socket_path);
    }
    let listener = UnixListener::bind(socket_path)?;
    for stream in listener.incoming() {
        if should_shutdown() {
            break;
        }
        match stream {
            Ok(stream) => handle_stream(stream)?,
            Err(err) => return Err(err.into()),
        }
    }
    Ok(())
}

#[cfg(not(unix))]
pub fn serve_with_shutdown<F>(_socket_path: &Path, _should_shutdown: F) -> anyhow::Result<()>
where
    F: Fn() -> bool,
{
    anyhow::bail!("ccb-runtime-accelerator socket server requires Unix sockets")
}

#[cfg(unix)]
fn handle_stream(mut stream: UnixStream) -> anyhow::Result<()> {
    let mut line = String::new();
    {
        let mut reader = BufReader::new(&stream);
        reader.read_line(&mut line)?;
    }
    let response = handle_request_line(&line);
    stream.write_all(response_line(&response).as_bytes())?;
    Ok(())
}

pub fn baseline_snapshot(project_root: &str) -> anyhow::Result<BaselineSnapshot> {
    let rows = ps_rows()?;
    let project_root = project_root.trim().to_string();
    let mut processes: Vec<ProcessSample> = rows
        .into_iter()
        .filter(|sample| include_process(sample, &project_root))
        .collect();
    processes.sort_by_key(|sample| sample.pid);
    Ok(BaselineSnapshot {
        schema_version: SCHEMA_VERSION,
        project_root,
        process_count: processes.len(),
        processes,
    })
}

pub fn codex_observe(params: &Value) -> anyhow::Result<CodexObserveResponse> {
    let params: CodexObserveParams = serde_json::from_value(params.clone())?;
    let observations = params.jobs.iter().map(observe_codex_job).collect();
    Ok(CodexObserveResponse {
        schema_version: SCHEMA_VERSION,
        observations,
    })
}

fn observe_codex_job(job: &CodexObserveJob) -> CodexJobObservation {
    let mut state = job.state.clone();
    state.session_path = job.session_path.clone();
    let mut items = Vec::new();
    let mut reached_terminal = false;
    let path = Path::new(&job.session_path);
    let result = read_codex_entries(path, &mut state, |entry, state| {
        if process_codex_entry(job, state, &mut items, entry) {
            reached_terminal = true;
        }
        !reached_terminal
    });
    CodexJobObservation {
        job_id: job.job_id.clone(),
        session_path: job.session_path.clone(),
        state,
        items,
        reached_terminal,
        error: result.err().map(|err| err.to_string()),
    }
}

fn read_codex_entries<F>(
    path: &Path,
    state: &mut CodexObservationState,
    mut visit: F,
) -> anyhow::Result<()>
where
    F: FnMut(NormalizedCodexEntry, &mut CodexObservationState) -> bool,
{
    let mut reader = BufReader::new(File::open(path)?);
    let size = reader.get_ref().metadata()?.len();
    if state.offset > size {
        state.offset = size;
    }
    reader.seek(SeekFrom::Start(state.offset))?;
    let mut line = String::new();
    loop {
        line.clear();
        let bytes = reader.read_line(&mut line)?;
        if bytes == 0 {
            break;
        }
        state.offset += bytes as u64;
        let Ok(raw) = serde_json::from_str::<Value>(line.trim_end()) else {
            continue;
        };
        let Some(entry) = normalize_codex_entry(&raw) else {
            continue;
        };
        if !visit(entry, state) {
            break;
        }
    }
    Ok(())
}

#[derive(Debug, Clone, PartialEq)]
struct NormalizedCodexEntry {
    role: String,
    text: String,
    entry_type: String,
    payload_type: String,
    timestamp: String,
    phase: String,
    turn_id: String,
    task_id: String,
    reason: String,
    last_agent_message: String,
    error_message: String,
    error_category: String,
}

fn normalize_codex_entry(raw: &Value) -> Option<NormalizedCodexEntry> {
    let entry_type = value_string(raw.get("type"));
    let payload_value = raw.get("payload").cloned().unwrap_or_else(|| json!({}));
    let payload_type = value_string(payload_value.get("type"));
    let error_value = payload_value.get("error").filter(|value| value.is_object());
    let base = NormalizedCodexEntry {
        role: String::new(),
        text: String::new(),
        entry_type: entry_type.clone(),
        payload_type: payload_type.clone(),
        timestamp: value_string(raw.get("timestamp")),
        phase: value_string(payload_value.get("phase")),
        turn_id: value_string(payload_value.get("turn_id")),
        task_id: value_string(payload_value.get("task_id")),
        reason: value_string(payload_value.get("reason")),
        last_agent_message: value_string(payload_value.get("last_agent_message")),
        error_message: error_value
            .and_then(|value| value.get("message"))
            .map(|value| value_string(Some(value)))
            .unwrap_or_default(),
        error_category: error_value
            .and_then(|value| value.get("codex_error_info"))
            .map(|value| value_string(Some(value)))
            .unwrap_or_default(),
    };


    if entry_type == "response_item" && payload_type == "agent_message" {
        return None;
    }

    if entry_type == "response_item" && payload_type == "message" {
        let role = value_string(payload_value.get("role")).to_ascii_lowercase();
        let text = if role == "user" {
            join_content_text(payload_value.get("content"), "input_text")
        } else {
            join_content_text(payload_value.get("content"), "output_text")
                .or_else(|| join_content_text(payload_value.get("content"), "text"))
                .or_else(|| first_text(&payload_value, &["message", "content", "text"]))
        };
        return entry_with_text(base, role, text);
    }

    if entry_type == "event_msg" {
        if payload_type == "sub_agent_activity" {
            return None;
        }
        if payload_type == "task_started" {
            return Some(NormalizedCodexEntry {
                role: "system".to_string(),
                reason: "task_started".to_string(),
                ..base
            });
        }
        if payload_type == "user_message" {
            return entry_with_text(
                base,
                "user".to_string(),
                first_text(&payload_value, &["message"]),
            );
        }
        if matches!(
            payload_type.as_str(),
            "agent_message" | "assistant_message" | "assistant" | "assistant_response" | "message"
        ) {
            let role = value_string(payload_value.get("role")).to_ascii_lowercase();
            let role = if role == "user" { "user" } else { "assistant" }.to_string();
            return entry_with_text(
                base,
                role,
                first_text(&payload_value, &["message", "content", "text"]),
            );
        }
        if payload_type == "task_complete" {
            return Some(NormalizedCodexEntry {
                role: "system".to_string(),
                text: value_string(payload_value.get("last_agent_message")),
                reason: "task_complete".to_string(),
                ..base
            });
        }
        if payload_type == "turn_aborted" {
            return Some(NormalizedCodexEntry {
                role: "system".to_string(),
                text: value_string(payload_value.get("message")),
                reason: value_string(payload_value.get("reason")),
                ..base
            });
        }
    }

    let role = value_string(payload_value.get("role")).to_ascii_lowercase();
    let text = if role == "user" {
        first_text(&payload_value, &["message", "content", "text"])
    } else {
        first_text(&payload_value, &["message", "content", "text"])
    };
    entry_with_text(base, role, text)
}

fn entry_with_text(
    base: NormalizedCodexEntry,
    role: String,
    text: Option<String>,
) -> Option<NormalizedCodexEntry> {
    let text = text.unwrap_or_default().trim().to_string();
    if role.is_empty() || text.is_empty() {
        return None;
    }
    Some(NormalizedCodexEntry { role, text, ..base })
}

fn process_codex_entry(
    job: &CodexObserveJob,
    state: &mut CodexObservationState,
    items: &mut Vec<CodexCompletionItem>,
    entry: NormalizedCodexEntry,
) -> bool {
    let anchor_needle = format!("{REQ_ID_PREFIX} {}", job.request_anchor);
    let pending_turn_candidate = !state.anchor_seen
        && (entry.payload_type == "task_started"
            || (entry.role == "user"
                && !job.request_anchor.is_empty()
                && entry.text.contains(&anchor_needle)));
    if !entry.turn_id.is_empty() && (state.bound_turn_id.is_empty() || pending_turn_candidate) {
        state.bound_turn_id = entry.turn_id.clone();
    }
    if !entry.task_id.is_empty() && (state.bound_task_id.is_empty() || pending_turn_candidate) {
        state.bound_task_id = entry.task_id.clone();
    }
    if !entry.turn_id.is_empty()
        && !state.bound_turn_id.is_empty()
        && entry.turn_id != state.bound_turn_id
    {
        return false;
    }

    if entry.role == "user" {
        if !job.request_anchor.is_empty()
            && entry.text.contains(&anchor_needle)
            && !state.anchor_seen
        {
            let mut payload = json!({"turn_id": turn_id_or_anchor(state, &job.request_anchor)});
            add_optional_payload_fields(&mut payload, state);
            push_item(job, state, items, "anchor_seen", payload);
            state.anchor_seen = true;
        }
        return false;
    }

    if !state.anchor_seen {
        return false;
    }

    if entry.role == "assistant" {
        return process_assistant_entry(job, state, items, &entry);
    }

    let terminal_type = if entry.payload_type.is_empty() {
        entry.entry_type.as_str()
    } else {
        entry.payload_type.as_str()
    };
    match terminal_type {
        "task_complete" => {
            if !entry.error_message.trim().is_empty()
                || !entry.error_category.trim().is_empty()
            {
                // A terminal provider error (e.g. a safety stop) must not
                // surface as a successful completion, and earlier progress
                // must not be promoted to a final handback.
                let mut payload = json!({
                    "reason": "task_complete_error",
                    "status": "failed",
                    "last_agent_message": selected_reply(state),
                    "turn_id": turn_id_or_anchor(state, &job.request_anchor),
                });
                let error_message = entry.error_message.trim();
                if !error_message.is_empty() {
                    payload["text"] = json!(error_message);
                    payload["error_message"] = json!(error_message);
                }
                let error_category = entry.error_category.trim();
                if !error_category.is_empty() {
                    payload["provider_error_category"] = json!(error_category);
                    payload["error_type"] = json!(error_category);
                }
                add_optional_payload_fields(&mut payload, state);
                push_item(job, state, items, "turn_aborted", payload);
                return true;
            }
            let terminal_text = entry.last_agent_message.trim();
            if !terminal_text.is_empty() {
                state.last_agent_message = clean_reply_text(terminal_text, &job.request_anchor);
            }
            let mut payload = json!({
                "reason": "task_complete",
                "last_agent_message": selected_reply(state),
                "turn_id": turn_id_or_anchor(state, &job.request_anchor),
            });
            add_optional_payload_fields(&mut payload, state);
            push_item(job, state, items, "turn_boundary", payload);
            true
        }
        "turn_aborted" => {
            let reason = if entry.reason.trim().is_empty() {
                "turn_aborted".to_string()
            } else {
                entry.reason.trim().to_string()
            };
            let status = if reason.to_ascii_lowercase().contains("cancel")
                || reason.to_ascii_lowercase().contains("abort")
                || reason.to_ascii_lowercase().contains("interrupt")
            {
                "cancelled"
            } else {
                "failed"
            };
            let mut payload = json!({
                "reason": reason,
                "status": status,
                "last_agent_message": selected_reply(state),
                "turn_id": turn_id_or_anchor(state, &job.request_anchor),
            });
            if !entry.text.trim().is_empty() {
                payload["text"] = json!(entry.text.trim());
                payload["error_message"] = json!(entry.text.trim());
            }
            add_optional_payload_fields(&mut payload, state);
            push_item(job, state, items, "turn_aborted", payload);
            true
        }
        _ => false,
    }
}

fn process_assistant_entry(
    job: &CodexObserveJob,
    state: &mut CodexObservationState,
    items: &mut Vec<CodexCompletionItem>,
    entry: &NormalizedCodexEntry,
) -> bool {
    let signature = assistant_signature(entry);
    if !signature.is_empty() {
        if signature == state.last_assistant_signature {
            return false;
        }
        state.last_assistant_signature = signature;
    }
    let cleaned = clean_reply_text(&entry.text, &job.request_anchor);
    if cleaned.is_empty() {
        return false;
    }
    state.reply_buffer = append_reply_text(&state.reply_buffer, &cleaned);
    state.last_assistant_message = cleaned.clone();
    if entry.phase == "final_answer" {
        state.last_final_answer = cleaned.clone();
    }
    let mut payload = json!({
        "text": cleaned,
        "merged_text": state.reply_buffer,
    });
    add_optional_payload_fields(&mut payload, state);
    if !entry.phase.is_empty() {
        payload["phase"] = json!(entry.phase);
    }
    push_item(job, state, items, "assistant_chunk", payload);
    false
}

fn push_item(
    job: &CodexObserveJob,
    state: &mut CodexObservationState,
    items: &mut Vec<CodexCompletionItem>,
    kind: &str,
    payload: Value,
) {
    items.push(CodexCompletionItem {
        job_id: job.job_id.clone(),
        kind: kind.to_string(),
        seq: state.next_seq,
        payload,
    });
    state.next_seq += 1;
}

fn add_optional_payload_fields(payload: &mut Value, state: &CodexObservationState) {
    if !state.bound_task_id.is_empty() {
        payload["task_id"] = json!(state.bound_task_id);
    }
    if !state.session_path.is_empty() {
        payload["session_path"] = json!(state.session_path);
    }
}

fn turn_id_or_anchor(state: &CodexObservationState, request_anchor: &str) -> String {
    if state.bound_turn_id.is_empty() {
        request_anchor.to_string()
    } else {
        state.bound_turn_id.clone()
    }
}

fn assistant_signature(entry: &NormalizedCodexEntry) -> String {
    if entry.timestamp.is_empty() || entry.text.trim().is_empty() {
        String::new()
    } else {
        format!(
            "{}\0{}\0{}",
            entry.timestamp,
            entry.phase,
            entry.text.trim()
        )
    }
}

fn append_reply_text(reply_buffer: &str, cleaned: &str) -> String {
    if reply_buffer.is_empty() {
        cleaned.to_string()
    } else {
        format!("{reply_buffer}\n{cleaned}").trim().to_string()
    }
}

fn selected_reply(state: &CodexObservationState) -> String {
    for candidate in [
        &state.last_agent_message,
        &state.last_final_answer,
        &state.last_assistant_message,
        &state.reply_buffer,
    ] {
        let text = candidate.trim();
        if !text.is_empty() {
            return text.to_string();
        }
    }
    String::new()
}

fn clean_reply_text(text: &str, request_anchor: &str) -> String {
    let mut lines: Vec<&str> = text.lines().collect();
    while lines
        .last()
        .map(|line| line.trim().is_empty())
        .unwrap_or(false)
    {
        lines.pop();
    }
    if !request_anchor.is_empty()
        && lines
            .last()
            .map(|line| line.trim() == format!("{DONE_PREFIX} {request_anchor}"))
            .unwrap_or(false)
    {
        lines.pop();
    }
    while lines
        .last()
        .map(|line| line.trim().is_empty())
        .unwrap_or(false)
    {
        lines.pop();
    }
    lines.join("\n").trim().to_string()
}

fn value_string(value: Option<&Value>) -> String {
    value
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string()
}

fn first_text(payload: &Value, keys: &[&str]) -> Option<String> {
    for key in keys {
        let Some(value) = payload.get(*key) else {
            continue;
        };
        if let Some(text) = value
            .as_str()
            .map(str::trim)
            .filter(|text| !text.is_empty())
        {
            return Some(text.to_string());
        }
    }
    None
}

fn join_content_text(content: Option<&Value>, item_type: &str) -> Option<String> {
    let items = content?.as_array()?;
    let texts: Vec<String> = items
        .iter()
        .filter_map(|item| {
            if value_string(item.get("type")) == item_type {
                first_text(item, &["text"])
            } else {
                None
            }
        })
        .collect();
    if texts.is_empty() {
        None
    } else {
        Some(texts.join("\n").trim().to_string())
    }
}

fn ps_rows() -> anyhow::Result<Vec<ProcessSample>> {
    let output = Command::new("ps")
        .args(["-eo", "pid=,ppid=,pcpu=,rss=,comm=,args="])
        .output()?;
    if !output.status.success() {
        anyhow::bail!("ps failed with status {}", output.status);
    }
    let text = String::from_utf8_lossy(&output.stdout);
    Ok(text.lines().filter_map(parse_ps_line).collect())
}

fn parse_ps_line(line: &str) -> Option<ProcessSample> {
    let mut parts = line.split_whitespace();
    let pid = parts.next()?.parse().ok()?;
    let ppid = parts.next()?.parse().ok()?;
    let cpu_percent = parts.next()?.parse().ok()?;
    let rss_kb = parts.next()?.parse().ok()?;
    let command = parts.next()?.to_string();
    let args = parts.collect::<Vec<_>>().join(" ");
    let kind = classify_process(&command, &args);
    Some(ProcessSample {
        pid,
        ppid,
        cpu_percent,
        rss_kb,
        command,
        args,
        kind,
    })
}

fn include_process(sample: &ProcessSample, project_root: &str) -> bool {
    if sample.kind == "other" {
        return false;
    }
    if project_root.is_empty() {
        return true;
    }
    sample.args.contains(project_root)
}

pub fn classify_process(command: &str, args: &str) -> String {
    let text = format!("{command} {args}").to_ascii_lowercase();
    if text.contains("ccb-runtime-accelerator") {
        "accelerator".to_string()
    } else if text.contains("provider_backends.codex.bridge")
        || text.contains("codex.bridge")
        || text.contains("bridge_runtime")
    {
        "codex_bridge".to_string()
    } else if text.contains("ccbd") || text.contains("ccb-daemon") {
        "ccbd".to_string()
    } else if command.contains("codex") || text.contains(" codex ") {
        "codex_cli".to_string()
    } else {
        "other".to_string()
    }
}

pub fn default_socket_path(project_root: &Path) -> PathBuf {
    let root = project_root
        .canonicalize()
        .unwrap_or_else(|_| project_root.to_path_buf());
    let preferred = root
        .join(".ccb")
        .join("runtime-accelerator")
        .join("accelerator.sock");
    if unix_socket_path_is_safe(&preferred) {
        return preferred;
    }
    runtime_socket_path(&root)
}

fn runtime_socket_path(project_root: &Path) -> PathBuf {
    let key = project_socket_key(project_root);
    for root in runtime_socket_roots() {
        let candidate = root.join(format!("accelerator-{key}.sock"));
        if unix_socket_path_is_safe(&candidate) {
            return candidate;
        }
    }
    PathBuf::from("/tmp")
        .join("ccb-runtime")
        .join(format!("accelerator-{key}.sock"))
}

fn runtime_socket_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Ok(raw) = std::env::var("XDG_RUNTIME_DIR") {
        if !raw.trim().is_empty() {
            roots.push(PathBuf::from(raw).join("ccb-runtime"));
        }
    }
    roots.push(PathBuf::from("/tmp").join("ccb-runtime"));
    let temp_root = std::env::temp_dir().join("ccb-runtime");
    if !roots.iter().any(|root| root == &temp_root) {
        roots.push(temp_root);
    }
    roots
}

fn unix_socket_path_is_safe(path: &Path) -> bool {
    path.as_os_str().as_bytes().len() <= 100
}

fn project_socket_key(project_root: &Path) -> String {
    let mut hasher = Fnv1a64::new();
    hasher.write(project_root.to_string_lossy().as_bytes());
    format!("{:016x}", hasher.finish())
}

struct Fnv1a64(u64);

impl Fnv1a64 {
    fn new() -> Self {
        Self(0xcbf29ce484222325)
    }
}

impl Hasher for Fnv1a64 {
    fn finish(&self) -> u64 {
        self.0
    }

    fn write(&mut self, bytes: &[u8]) {
        for byte in bytes {
            self.0 ^= u64::from(*byte);
            self.0 = self.0.wrapping_mul(0x100000001b3);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ping_uses_daemon_like_response_shape() {
        let response = handle_request_line(r#"{"method":"ping","params":{}}"#);
        assert!(response.ok);
        let result = response.result.unwrap();
        assert_eq!(result["service"], "ccb-runtime-accelerator");
        assert_eq!(result["hot_loop_replacement_active"], Value::Null);
    }

    #[test]
    fn capabilities_report_hot_loop_replacement_active() {
        let response = handle_request_line(r#"{"method":"capabilities","params":{}}"#);
        assert!(response.ok);
        let result = response.result.unwrap();
        assert_eq!(result["hot_loop_replacement_active"], true);
        assert!(result["capabilities"]
            .as_array()
            .unwrap()
            .contains(&json!("codex_observe")));
    }

    #[test]
    fn unknown_method_fails_loudly() {
        let response = handle_request_line(r#"{"method":"replace_everything","params":{}}"#);
        assert!(!response.ok);
        assert!(response.error.unwrap().contains("unknown method"));
    }

    #[test]
    fn classifies_hot_loop_processes() {
        assert_eq!(
            classify_process("python", "-m provider_backends.codex.bridge /repo"),
            "codex_bridge"
        );
        assert_eq!(classify_process("ccbd", "--project /repo"), "ccbd");
        assert_eq!(classify_process("codex", "--sandbox danger"), "codex_cli");
    }

    #[test]
    fn default_socket_lives_under_project_ccb() {
        assert_eq!(
            default_socket_path(Path::new("/repo")),
            PathBuf::from("/repo/.ccb/runtime-accelerator/accelerator.sock")
        );
    }

    #[test]
    fn default_socket_falls_back_for_long_project_paths() {
        let long_root = PathBuf::from(format!("/tmp/{}", "long-project-name-".repeat(8)));
        let socket_path = default_socket_path(&long_root);

        assert!(socket_path
            .file_name()
            .unwrap()
            .to_string_lossy()
            .starts_with("accelerator-"));
        assert!(socket_path
            .file_name()
            .unwrap()
            .to_string_lossy()
            .ends_with(".sock"));
        assert!(socket_path.as_os_str().as_bytes().len() <= 100);
    }

    #[test]
    fn codex_observe_reads_anchor_reply_and_terminal_boundary() {
        let path = unique_test_path("codex-observe.jsonl");
        std::fs::write(
            &path,
            [
                json!({
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "historical-turn"}
                })
                .to_string(),
                json!({
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "CCB_REQ_ID: req-1\nhello", "turn_id": "turn-1"}
                })
                .to_string(),
                json!({
                    "type": "response_item",
                    "timestamp": "2026-06-26T00:00:00Z",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "partial"}],
                        "turn_id": "turn-1"
                    }
                })
                .to_string(),
                json!({
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "last_agent_message": "final\nCCB_DONE: req-1", "turn_id": "turn-1"}
                })
                .to_string(),
            ]
            .join("\n")
                + "\n",
        )
        .unwrap();

        let response = codex_observe(&json!({
            "jobs": [{
                "job_id": "job-1",
                "session_path": path,
                "request_anchor": "req-1",
                "state": {"offset": 0, "next_seq": 1}
            }]
        }))
        .unwrap();

        let observation = &response.observations[0];
        assert!(observation.error.is_none());
        assert!(observation.reached_terminal);
        assert!(observation.state.anchor_seen);
        assert!(observation.state.offset > 0);
        assert_eq!(
            observation
                .items
                .iter()
                .map(|item| item.kind.as_str())
                .collect::<Vec<_>>(),
            vec!["anchor_seen", "assistant_chunk", "turn_boundary"]
        );
        assert_eq!(observation.items[2].payload["last_agent_message"], "final");

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn codex_observe_ignores_native_subagent_reply_and_foreign_terminal() {
        let path = unique_test_path("codex-observe-subagent.jsonl");
        std::fs::write(
            &path,
            [
                json!({
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "parent-turn"}
                })
                .to_string(),
                json!({
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "CCB_REQ_ID: req-1\nhello"}
                })
                .to_string(),
                json!({
                    "type": "response_item",
                    "payload": {
                        "type": "agent_message",
                        "author": "/root/child",
                        "recipient": "/root",
                        "content": [{"type": "input_text", "text": "child final"}],
                        "turn_id": "child-turn"
                    }
                })
                .to_string(),
                json!({
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "last_agent_message": "child final", "turn_id": "child-turn"}
                })
                .to_string(),
                json!({
                    "type": "response_item",
                    "timestamp": "2026-07-13T00:00:00Z",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "phase": "final_answer",
                        "content": [{"type": "output_text", "text": "parent final"}],
                        "turn_id": "parent-turn"
                    }
                })
                .to_string(),
                json!({
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "last_agent_message": "parent final", "turn_id": "parent-turn"}
                })
                .to_string(),
            ]
            .join("\n")
                + "\n",
        )
        .unwrap();

        let response = codex_observe(&json!({
            "jobs": [{
                "job_id": "job-1",
                "session_path": path,
                "request_anchor": "req-1",
                "state": {"offset": 0, "next_seq": 1}
            }]
        }))
        .unwrap();

        let observation = &response.observations[0];
        assert!(observation.reached_terminal);
        assert_eq!(observation.state.bound_turn_id, "parent-turn");
        assert_eq!(observation.items.len(), 3);
        assert_eq!(observation.items[1].payload["text"], "parent final");
        assert_eq!(
            observation.items[2].payload["last_agent_message"],
            "parent final"
        );
        assert!(!observation
            .items
            .iter()
            .any(|item| item.payload.to_string().contains("child final")));

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn codex_observe_task_complete_error_fails_turn_without_success_boundary() {
        let path = std::env::var_os("CCB_TEST_TMPDIR")
            .map(PathBuf::from)
            .unwrap_or_else(std::env::temp_dir)
            .join(format!(
                "ccb-runtime-accelerator-{}-task-complete-error.jsonl",
                std::process::id()
            ));
        std::fs::write(
            &path,
            [
                json!({
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "CCB_REQ_ID: req-1\ndo the work", "turn_id": "turn-1"}
                })
                .to_string(),
                json!({
                    "type": "response_item",
                    "timestamp": "2026-09-12T00:00:00Z",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "I will start the work now."}],
                        "turn_id": "turn-1"
                    }
                })
                .to_string(),
                json!({
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "last_agent_message": null,
                        "turn_id": "turn-1",
                        "error": {
                            "message": "This request was blocked by our safety systems. Reason: Potentially unintended activity.",
                            "codex_error_info": "misalignment_policy_violation"
                        }
                    }
                })
                .to_string(),
            ]
            .join("\n")
                + "\n",
        )
        .unwrap();

        let response = codex_observe(&json!({
            "jobs": [{
                "job_id": "job-1",
                "session_path": path,
                "request_anchor": "req-1",
                "state": {"offset": 0, "next_seq": 1}
            }]
        }))
        .unwrap();

        let observation = &response.observations[0];
        assert!(observation.reached_terminal);
        assert!(!observation.items.iter().any(|item| item.kind == "turn_boundary"));
        let terminal = observation
            .items
            .iter()
            .find(|item| item.kind == "turn_aborted")
            .expect("task_complete.error must emit a turn_aborted item");
        assert_eq!(terminal.payload["reason"], "task_complete_error");
        assert_eq!(terminal.payload["status"], "failed");
        assert_eq!(
            terminal.payload["provider_error_category"],
            "misalignment_policy_violation"
        );
        assert_eq!(
            terminal.payload["error_type"],
            "misalignment_policy_violation"
        );
        assert!(terminal.payload["error_message"]
            .as_str()
            .unwrap_or("")
            .contains("blocked by our safety systems"));
        // Earlier progress stays as failure context, never as a success reply.
        assert_eq!(terminal.payload["last_agent_message"], "I will start the work now.");

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn codex_observe_does_not_emit_assistant_before_anchor() {
        let path = unique_test_path("codex-observe-before-anchor.jsonl");
        std::fs::write(
            &path,
            json!({
                "type": "response_item",
                "timestamp": "2026-06-26T00:00:00Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "not yet"}]
                }
            })
            .to_string()
                + "\n",
        )
        .unwrap();

        let response = codex_observe(&json!({
            "jobs": [{
                "job_id": "job-1",
                "session_path": path,
                "request_anchor": "req-1"
            }]
        }))
        .unwrap();

        let observation = &response.observations[0];
        assert!(observation.items.is_empty());
        assert!(!observation.state.anchor_seen);

        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn codex_observe_reports_missing_session_per_job() {
        let response = codex_observe(&json!({
            "jobs": [{
                "job_id": "job-1",
                "session_path": "/no/such/codex-session.jsonl",
                "request_anchor": "req-1"
            }]
        }))
        .unwrap();

        let observation = &response.observations[0];
        assert!(observation.items.is_empty());
        assert!(observation
            .error
            .as_deref()
            .unwrap_or("")
            .contains("No such file"));
    }

    fn unique_test_path(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "ccb-runtime-accelerator-{}-{name}",
            std::process::id()
        ))
    }
}
