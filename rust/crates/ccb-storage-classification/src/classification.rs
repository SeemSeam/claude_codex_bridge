use camino::{Utf8Path, Utf8PathBuf};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::os::unix::fs::MetadataExt;
use std::path::PathBuf;

use ccb_storage::path_helpers::RootKind;
use ccb_storage::paths::PathLayout;

pub const SCHEMA_VERSION: i32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StorageClass {
    Secret,
    Session,
    Authority,
    StartupAuthorityBundle,
    RuntimeEphemeral,
    Workspace,
    UserContent,
    ProjectedConfig,
    RebuildableCache,
    Residue,
    Unknown,
}

impl StorageClass {
    pub fn as_str(&self) -> &'static str {
        match self {
            StorageClass::Secret => "secret",
            StorageClass::Session => "session",
            StorageClass::Authority => "authority",
            StorageClass::StartupAuthorityBundle => "startup_authority_bundle",
            StorageClass::RuntimeEphemeral => "runtime_ephemeral",
            StorageClass::Workspace => "workspace",
            StorageClass::UserContent => "user_content",
            StorageClass::ProjectedConfig => "projected_config",
            StorageClass::RebuildableCache => "rebuildable_cache",
            StorageClass::Residue => "residue",
            StorageClass::Unknown => "unknown",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StorageEntry {
    pub path: String,
    pub relative_path: String,
    pub storage_class: StorageClass,
    pub size_bytes: i64,
    pub provider: Option<String>,
    pub agent: Option<String>,
    pub active: Option<bool>,
    pub is_active_version: Option<bool>,
    pub reachable_from_current_symlink: Option<bool>,
    pub reclaimable: Option<bool>,
    pub reason: Option<String>,
    pub root_kind: String,
}

impl StorageEntry {
    pub fn to_record(&self) -> serde_json::Map<String, serde_json::Value> {
        serde_json::to_value(self)
            .unwrap_or_default()
            .as_object()
            .cloned()
            .unwrap_or_default()
    }
}

const CCBD_AUTHORITY_FILES: &[&str] = &[
    "keeper.json",
    "lease.json",
    "lifecycle.json",
    "restore-report.json",
    "shutdown-intent.json",
    "shutdown-report.json",
    "start-policy.json",
    "startup-report.json",
    "state.json",
];

const CCBD_RUNTIME_DIRS: &[&str] = &["heartbeats", "leases", "cursors"];

const AGENT_AUTHORITY_FILES: &[&str] = &[
    "agent.json",
    "runtime.json",
    "helper.json",
    "restore.json",
    "provider.json",
];

const SECRET_FILENAMES: &[&str] = &[
    ".credentials.json",
    ".env",
    "auth.json",
    "google_accounts.json",
    "oauth_creds.json",
];

const CLAUDE_PROJECTED_NAMES: &[&str] = &["settings.json", "CLAUDE.md"];
const GEMINI_PROJECTED_NAMES: &[&str] = &["settings.json", "trustedFolders.json"];
const CODEX_PROJECTED_NAMES: &[&str] = &["config.toml"];
const OPENCODE_PROJECTED_NAMES: &[&str] = &["opencode.json"];
const MIMO_PROJECTED_NAMES: &[&str] = &["mimocode.json"];
const NATIVE_CLI_PROVIDERS: &[&str] = &["qwen", "cursor", "copilot", "crush", "kiro", "pi"];
const NATIVE_CLI_PROJECTED_ROOTS: &[&str] = &["inherited-skills", "role-skills"];
const NATIVE_CLI_CACHE_ROOTS: &[&str] = &[".cache", ".npm", ".tmp", "cache", "node_modules", "tmp"];
const NATIVE_CLI_SESSION_ROOTS: &[&str] = &[
    ".config", ".crush", ".cursor", ".kiro", ".local", ".pi", ".qwen", "data", "logs", "session",
    "sessions", "state",
];
const CODEX_SESSION_NAMES: &[&str] = &[
    ".ccb-session-namespace.json",
    "history.jsonl",
    "logs_2.sqlite",
    "logs_2.sqlite-shm",
    "logs_2.sqlite-wal",
    "state_5.sqlite",
    "state_5.sqlite-shm",
    "state_5.sqlite-wal",
];

pub fn summarize_storage(
    layout: &PathLayout,
) -> ccb_storage::Result<serde_json::Map<String, serde_json::Value>> {
    let entries = scan_layout(layout)?;
    Ok(summary_payload(layout, &entries))
}

fn scan_layout(layout: &PathLayout) -> ccb_storage::Result<Vec<StorageEntry>> {
    let mut roots: Vec<(&str, Utf8PathBuf)> = vec![("project", layout.ccb_dir())];
    if layout.runtime_state_root() != layout.ccb_dir().as_path() {
        roots.push(("runtime", layout.runtime_state_root().to_path_buf()));
    }

    let mut entries = Vec::new();
    let mut seen = HashMap::<(u64, u64), ()>::new();
    for (root_kind, root) in roots {
        if !root.exists() {
            continue;
        }
        for path in walk_files(&root)? {
            let identity = scan_identity(&path)?;
            if seen.contains_key(&identity) {
                continue;
            }
            seen.insert(identity, ());
            entries.push(classify_path(layout, &root, &path, root_kind)?);
        }
    }
    Ok(entries)
}

fn walk_files(root: &Utf8Path) -> std::io::Result<Vec<Utf8PathBuf>> {
    let mut results = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        for entry in fs::read_dir(&dir)? {
            let entry = entry?;
            let path = entry.path();
            let metadata = entry.metadata()?;
            if metadata.is_symlink() {
                if let Ok(p) = Utf8PathBuf::from_path_buf(path) {
                    results.push(p);
                }
                continue;
            }
            if metadata.is_dir() {
                if let Ok(p) = Utf8PathBuf::from_path_buf(path) {
                    stack.push(p);
                }
            } else if metadata.is_file() {
                if let Ok(p) = Utf8PathBuf::from_path_buf(path) {
                    results.push(p);
                }
            }
        }
    }
    Ok(results)
}

fn classify_path(
    layout: &PathLayout,
    root: &Utf8Path,
    path: &Utf8Path,
    root_kind: &str,
) -> ccb_storage::Result<StorageEntry> {
    let size = safe_size(path);
    let relative_path = relative_display(layout, root, path, root_kind);
    if is_allowed_provider_secret_symlink(path, layout) {
        return classify_relative(layout, path, &relative_path, size, root_kind);
    }
    if let Some(reason) = unsafe_symlink_reason(path, layout) {
        if !is_marked_projected_symlink(path) {
            return Ok(entry(
                path,
                &relative_path,
                StorageClass::Unknown,
                size,
                root_kind,
                None,
                None,
                None,
                None,
                None,
                None,
                Some(reason),
            ));
        }
    }
    classify_relative(layout, path, &relative_path, size, root_kind)
}

fn classify_relative(
    _layout: &PathLayout,
    path: &Utf8Path,
    relative_path: &str,
    size: i64,
    root_kind: &str,
) -> ccb_storage::Result<StorageEntry> {
    let parts: Vec<&str> = relative_path.split('/').collect();
    if parts.is_empty() {
        return Ok(entry(
            path,
            relative_path,
            StorageClass::Unknown,
            size,
            root_kind,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ));
    }

    if parts[0] == "ccb.config" {
        return Ok(entry(
            path,
            relative_path,
            StorageClass::Authority,
            size,
            root_kind,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ));
    }
    if parts[0] == "ccb_memory.md" {
        return Ok(entry(
            path,
            relative_path,
            StorageClass::UserContent,
            size,
            root_kind,
            None,
            None,
            None,
            None,
            None,
            None,
            Some("project_shared_memory".into()),
        ));
    }
    if parts[0] == "runtime-root.json" || parts[0] == "runtime-root-ref.json" {
        return Ok(entry(
            path,
            relative_path,
            StorageClass::Authority,
            size,
            root_kind,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ));
    }
    if parts[0].starts_with('.') && parts[0].ends_with("-session") {
        let filename = parts[0]
            .trim_start_matches('.')
            .trim_end_matches("-session");
        let segments: Vec<&str> = filename.split('-').collect();
        let provider = segments.first().map(|s| s.to_string());
        let agent = if segments.len() >= 3 {
            Some(segments[1].to_string())
        } else {
            None
        };
        return Ok(entry(
            path,
            relative_path,
            StorageClass::Session,
            size,
            root_kind,
            provider,
            agent,
            None,
            None,
            None,
            None,
            None,
        ));
    }
    if parts.len() >= 2 && parts[0] == "ccbd" {
        return Ok(classify_ccbd(path, relative_path, &parts, size, root_kind));
    }
    if parts.len() >= 3 && parts[0] == "agents" {
        return Ok(classify_agent(path, relative_path, &parts, size, root_kind));
    }
    if parts.len() >= 3 && parts[0] == "provider-profiles" {
        let provider = parts[2];
        let agent = parts[1];
        let remainder: Vec<&str> = parts[3..].to_vec();
        return Ok(classify_provider_home(
            path,
            relative_path,
            provider,
            agent,
            &remainder,
            size,
            root_kind,
        ));
    }
    if parts == ["state", "memory.seed.json"] {
        return Ok(entry(
            path,
            relative_path,
            StorageClass::Authority,
            size,
            root_kind,
            None,
            None,
            None,
            None,
            None,
            None,
            Some("project_memory_seed".into()),
        ));
    }
    if parts.len() >= 5 && parts[0] == "runtime" && parts[1] == "skills" {
        return Ok(entry(
            path,
            relative_path,
            StorageClass::ProjectedConfig,
            size,
            root_kind,
            Some(parts[3].to_string()),
            Some(parts[2].to_string()),
            None,
            None,
            None,
            None,
            Some("provider_skill_instruction".into()),
        ));
    }
    if parts.len() == 3
        && parts[0] == "runtime"
        && parts[1] == "memory"
        && parts[2].ends_with(".md")
    {
        let agent = parts[2][..parts[2].len() - 3].to_string();
        return Ok(entry(
            path,
            relative_path,
            StorageClass::RuntimeEphemeral,
            size,
            root_kind,
            None,
            Some(agent),
            None,
            None,
            None,
            None,
            Some("project_memory_bundle".into()),
        ));
    }
    if parts.len() >= 2 && parts[0] == "shared-cache" {
        return Ok(entry(
            path,
            relative_path,
            StorageClass::RebuildableCache,
            size,
            root_kind,
            Some(parts[1].to_string()),
            None,
            None,
            None,
            None,
            Some(false),
            Some("shared_cache".into()),
        ));
    }
    if parts[0] == "workspaces" {
        return Ok(entry(
            path,
            relative_path,
            StorageClass::Workspace,
            size,
            root_kind,
            None,
            None,
            None,
            None,
            None,
            None,
            Some("agent_workspace".into()),
        ));
    }
    if parts[0] == "history" {
        return Ok(entry(
            path,
            relative_path,
            StorageClass::UserContent,
            size,
            root_kind,
            None,
            None,
            None,
            None,
            None,
            None,
            Some("project_history".into()),
        ));
    }
    Ok(entry(
        path,
        relative_path,
        StorageClass::Unknown,
        size,
        root_kind,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    ))
}

fn classify_ccbd(
    path: &Utf8Path,
    relative_path: &str,
    parts: &[&str],
    size: i64,
    root_kind: &str,
) -> StorageEntry {
    let name = parts[parts.len() - 1];
    let top = parts[1];
    if parts.len() == 2 && CCBD_AUTHORITY_FILES.contains(&name) {
        return entry(
            path,
            relative_path,
            StorageClass::Authority,
            size,
            root_kind,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        );
    }
    if CCBD_RUNTIME_DIRS.contains(&top)
        || name.ends_with(".pid")
        || name.ends_with(".sock")
        || name.ends_with(".lock")
    {
        return entry(
            path,
            relative_path,
            StorageClass::RuntimeEphemeral,
            size,
            root_kind,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        );
    }
    if [
        "mailboxes",
        "messages",
        "attempts",
        "replies",
        "executions",
        "snapshots",
    ]
    .contains(&top)
    {
        return entry(
            path,
            relative_path,
            StorageClass::Authority,
            size,
            root_kind,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        );
    }
    if name.ends_with(".jsonl") || name.ends_with(".log") {
        return entry(
            path,
            relative_path,
            StorageClass::Authority,
            size,
            root_kind,
            None,
            None,
            None,
            None,
            None,
            None,
            Some("ccbd_event_log".into()),
        );
    }
    if name.ends_with(".json") {
        return entry(
            path,
            relative_path,
            StorageClass::Authority,
            size,
            root_kind,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        );
    }
    entry(
        path,
        relative_path,
        StorageClass::Unknown,
        size,
        root_kind,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
}

fn classify_agent(
    path: &Utf8Path,
    relative_path: &str,
    parts: &[&str],
    size: i64,
    root_kind: &str,
) -> StorageEntry {
    let agent = parts[1].to_string();
    let name = parts[parts.len() - 1];
    if parts.len() == 3 && AGENT_AUTHORITY_FILES.contains(&name) {
        return entry(
            path,
            relative_path,
            StorageClass::Authority,
            size,
            root_kind,
            None,
            Some(agent),
            None,
            None,
            None,
            None,
            None,
        );
    }
    if parts.len() == 3 && name == "memory.md" {
        return entry(
            path,
            relative_path,
            StorageClass::UserContent,
            size,
            root_kind,
            None,
            Some(agent),
            None,
            None,
            None,
            None,
            Some("agent_private_memory".into()),
        );
    }
    if parts.len() == 3 && name.ends_with(".jsonl") {
        return entry(
            path,
            relative_path,
            StorageClass::Authority,
            size,
            root_kind,
            None,
            Some(agent),
            None,
            None,
            None,
            None,
            Some("agent_event_log".into()),
        );
    }
    if parts.len() >= 4 && parts[2] == "provider-runtime" {
        let provider = parts[3].to_string();
        return entry(
            path,
            relative_path,
            StorageClass::RuntimeEphemeral,
            size,
            root_kind,
            Some(provider),
            Some(agent),
            None,
            None,
            None,
            None,
            None,
        );
    }
    if parts.len() >= 5 && parts[2] == "provider-state" {
        let provider = parts[3].to_string();
        let remainder: Vec<&str> = if parts.len() >= 6 && parts[4] == "home" {
            parts[5..].to_vec()
        } else {
            parts[4..].to_vec()
        };
        return classify_provider_home(
            path,
            relative_path,
            &provider,
            &agent,
            &remainder,
            size,
            root_kind,
        );
    }
    if parts.len() >= 3 && parts[2] == "logs" {
        return entry(
            path,
            relative_path,
            StorageClass::RuntimeEphemeral,
            size,
            root_kind,
            None,
            Some(agent),
            None,
            None,
            None,
            None,
            Some("agent_log".into()),
        );
    }
    entry(
        path,
        relative_path,
        StorageClass::Unknown,
        size,
        root_kind,
        None,
        Some(agent),
        None,
        None,
        None,
        None,
        None,
    )
}

pub fn classify_provider_home(
    path: &Utf8Path,
    relative_path: &str,
    provider: &str,
    agent: &str,
    remainder: &[&str],
    size: i64,
    root_kind: &str,
) -> StorageEntry {
    let provider = provider.trim().to_lowercase();
    if provider.is_empty() {
        return entry(
            path,
            relative_path,
            StorageClass::ProjectedConfig,
            size,
            root_kind,
            None,
            Some(agent.to_string()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    if remainder.is_empty() {
        return entry(
            path,
            relative_path,
            StorageClass::ProjectedConfig,
            size,
            root_kind,
            Some(provider.clone()),
            Some(agent.to_string()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    let name = remainder[remainder.len() - 1];
    if SECRET_FILENAMES.contains(&name) {
        return entry(
            path,
            relative_path,
            StorageClass::Secret,
            size,
            root_kind,
            Some(provider.clone()),
            Some(agent.to_string()),
            None,
            None,
            None,
            None,
            Some("provider_secret".into()),
        );
    }
    if name.ends_with(".ccb-projection.json") {
        return entry(
            path,
            relative_path,
            StorageClass::ProjectedConfig,
            size,
            root_kind,
            Some(provider.clone()),
            Some(agent.to_string()),
            None,
            None,
            None,
            None,
            Some("projected_asset_marker".into()),
        );
    }
    match provider.as_str() {
        "codex" => classify_codex_home(
            path,
            relative_path,
            remainder,
            size,
            &provider,
            agent,
            root_kind,
        ),
        "claude" => classify_claude_home(
            path,
            relative_path,
            remainder,
            size,
            &provider,
            agent,
            root_kind,
        ),
        "gemini" => classify_gemini_home(
            path,
            relative_path,
            remainder,
            size,
            &provider,
            agent,
            root_kind,
        ),
        "opencode" => classify_opencode_home(
            path,
            relative_path,
            remainder,
            size,
            &provider,
            agent,
            root_kind,
        ),
        "droid" => classify_droid_home(
            path,
            relative_path,
            remainder,
            size,
            &provider,
            agent,
            root_kind,
        ),
        "kimi" => classify_kimi_home(
            path,
            relative_path,
            remainder,
            size,
            &provider,
            agent,
            root_kind,
        ),
        "mimo" => classify_mimo_home(
            path,
            relative_path,
            remainder,
            size,
            &provider,
            agent,
            root_kind,
        ),
        _ if NATIVE_CLI_PROVIDERS.contains(&provider.as_str()) => classify_native_cli_home(
            path,
            relative_path,
            remainder,
            size,
            &provider,
            agent,
            root_kind,
        ),
        _ => entry(
            path,
            relative_path,
            StorageClass::Unknown,
            size,
            root_kind,
            Some(provider),
            Some(agent.to_string()),
            None,
            None,
            None,
            None,
            None,
        ),
    }
}

fn classify_codex_home(
    path: &Utf8Path,
    relative_path: &str,
    remainder: &[&str],
    size: i64,
    provider: &str,
    agent: &str,
    root_kind: &str,
) -> StorageEntry {
    let name = remainder[remainder.len() - 1];
    if remainder[0] == "sessions"
        || CODEX_SESSION_NAMES.contains(&name)
        || remainder[0] == "log"
        || remainder[0] == "logs"
        || remainder[0] == "shell_snapshots"
    {
        return entry(
            path,
            relative_path,
            StorageClass::Session,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    if remainder[0] == ".tmp" && remainder.len() >= 2 && remainder[1] == "plugins" {
        return entry(
            path,
            relative_path,
            StorageClass::StartupAuthorityBundle,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            Some(false),
            Some("codex_plugin_bundle".into()),
        );
    }
    if name == "plugins.sha" && remainder.first() == Some(&".tmp") {
        return entry(
            path,
            relative_path,
            StorageClass::StartupAuthorityBundle,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            Some(false),
            Some("codex_plugin_bundle_manifest".into()),
        );
    }
    if CODEX_PROJECTED_NAMES.contains(&name)
        || remainder[0] == "skills"
        || remainder[0] == "commands"
    {
        return entry(
            path,
            relative_path,
            StorageClass::ProjectedConfig,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    if remainder[0] == ".tmp" || remainder[0] == ".cache" {
        return entry(
            path,
            relative_path,
            StorageClass::RebuildableCache,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    entry(
        path,
        relative_path,
        StorageClass::Unknown,
        size,
        root_kind,
        Some(provider.into()),
        Some(agent.into()),
        None,
        None,
        None,
        None,
        None,
    )
}

fn classify_claude_home(
    path: &Utf8Path,
    relative_path: &str,
    remainder: &[&str],
    size: i64,
    provider: &str,
    agent: &str,
    root_kind: &str,
) -> StorageEntry {
    let name = remainder[remainder.len() - 1];
    if name == ".claude.json" {
        return entry(
            path,
            relative_path,
            StorageClass::Session,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            Some("claude_trust_authority".into()),
        );
    }
    if remainder.len() >= 2 && remainder[0] == "Library" && remainder[1] == "Keychains" {
        return entry(
            path,
            relative_path,
            StorageClass::Secret,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            Some("macos_keychain_link".into()),
        );
    }
    if remainder.len() >= 4
        && remainder[0] == ".local"
        && remainder[1] == "share"
        && remainder[2] == "claude"
        && remainder[3] == "versions"
    {
        let is_active = claude_version_active(path, remainder);
        return entry(
            path,
            relative_path,
            StorageClass::RebuildableCache,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            Some(false),
            Some(is_active),
            Some(is_active),
            if is_active { Some(false) } else { None },
            if is_active {
                Some("active_claude_version_cache".into())
            } else {
                Some("claude_version_cache".into())
            },
        );
    }
    if remainder.len() >= 2 && remainder[0] == ".local" && remainder[1] == "bin" && name == "claude"
    {
        return entry(
            path,
            relative_path,
            StorageClass::RebuildableCache,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            Some(true),
            Some(false),
            Some(true),
            Some(false),
            Some("claude_current_binary_link".into()),
        );
    }
    if remainder[0] == ".claude"
        && remainder.len() >= 2
        && ["projects", "session-env", "tasks"].contains(&remainder[1])
    {
        return entry(
            path,
            relative_path,
            StorageClass::Session,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    if remainder[0] == ".claude"
        && (CLAUDE_PROJECTED_NAMES.contains(&name)
            || (remainder.len() >= 2 && (remainder[1] == "skills" || remainder[1] == "commands")))
    {
        return entry(
            path,
            relative_path,
            StorageClass::ProjectedConfig,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    if remainder[0] == ".cache" || remainder[0] == ".npm" {
        return entry(
            path,
            relative_path,
            StorageClass::RebuildableCache,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    entry(
        path,
        relative_path,
        StorageClass::Unknown,
        size,
        root_kind,
        Some(provider.into()),
        Some(agent.into()),
        None,
        None,
        None,
        None,
        None,
    )
}

fn classify_gemini_home(
    path: &Utf8Path,
    relative_path: &str,
    remainder: &[&str],
    size: i64,
    provider: &str,
    agent: &str,
    root_kind: &str,
) -> StorageEntry {
    let name = remainder[remainder.len() - 1];
    if remainder[0] == ".gemini" && remainder.len() >= 2 && remainder[1] == "tmp" {
        return entry(
            path,
            relative_path,
            StorageClass::Session,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    if remainder[0] == ".gemini" && GEMINI_PROJECTED_NAMES.contains(&name) {
        return entry(
            path,
            relative_path,
            StorageClass::ProjectedConfig,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    if remainder[0] == ".npm" {
        return entry(
            path,
            relative_path,
            StorageClass::RebuildableCache,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            Some("npm_cache".into()),
        );
    }
    if remainder.len() >= 2
        && remainder[0] == ".cache"
        && (remainder[1] == "node-gyp" || remainder[1] == "vscode-ripgrep")
    {
        return entry(
            path,
            relative_path,
            StorageClass::RebuildableCache,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            Some("tool_cache".into()),
        );
    }
    if remainder[0] == ".gemini" {
        return entry(
            path,
            relative_path,
            StorageClass::Session,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    entry(
        path,
        relative_path,
        StorageClass::Unknown,
        size,
        root_kind,
        Some(provider.into()),
        Some(agent.into()),
        None,
        None,
        None,
        None,
        None,
    )
}

fn classify_opencode_home(
    path: &Utf8Path,
    relative_path: &str,
    remainder: &[&str],
    size: i64,
    provider: &str,
    agent: &str,
    root_kind: &str,
) -> StorageEntry {
    let name = remainder[remainder.len() - 1];
    if OPENCODE_PROJECTED_NAMES.contains(&name) {
        return entry(
            path,
            relative_path,
            StorageClass::ProjectedConfig,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    if remainder[0] == ".cache" || remainder[0] == ".tmp" {
        return entry(
            path,
            relative_path,
            StorageClass::RebuildableCache,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    entry(
        path,
        relative_path,
        StorageClass::Unknown,
        size,
        root_kind,
        Some(provider.into()),
        Some(agent.into()),
        None,
        None,
        None,
        None,
        None,
    )
}

fn classify_droid_home(
    path: &Utf8Path,
    relative_path: &str,
    remainder: &[&str],
    size: i64,
    provider: &str,
    agent: &str,
    root_kind: &str,
) -> StorageEntry {
    if remainder[0] == "sessions" {
        return entry(
            path,
            relative_path,
            StorageClass::Session,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    if remainder[0] == "skills" {
        return entry(
            path,
            relative_path,
            StorageClass::ProjectedConfig,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    entry(
        path,
        relative_path,
        StorageClass::Unknown,
        size,
        root_kind,
        Some(provider.into()),
        Some(agent.into()),
        None,
        None,
        None,
        None,
        None,
    )
}

fn classify_kimi_home(
    path: &Utf8Path,
    relative_path: &str,
    remainder: &[&str],
    size: i64,
    provider: &str,
    agent: &str,
    root_kind: &str,
) -> StorageEntry {
    if NATIVE_CLI_PROJECTED_ROOTS.contains(&remainder[0]) {
        return entry(
            path,
            relative_path,
            StorageClass::ProjectedConfig,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    entry(
        path,
        relative_path,
        StorageClass::Unknown,
        size,
        root_kind,
        Some(provider.into()),
        Some(agent.into()),
        None,
        None,
        None,
        None,
        None,
    )
}

fn classify_mimo_home(
    path: &Utf8Path,
    relative_path: &str,
    remainder: &[&str],
    size: i64,
    provider: &str,
    agent: &str,
    root_kind: &str,
) -> StorageEntry {
    let name = remainder[remainder.len() - 1];
    if MIMO_PROJECTED_NAMES.contains(&name) {
        return entry(
            path,
            relative_path,
            StorageClass::ProjectedConfig,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    if remainder[0] == "data" || remainder[0] == "state" {
        return entry(
            path,
            relative_path,
            StorageClass::Session,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    if remainder[0] == "cache" {
        return entry(
            path,
            relative_path,
            StorageClass::RebuildableCache,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    entry(
        path,
        relative_path,
        StorageClass::Unknown,
        size,
        root_kind,
        Some(provider.into()),
        Some(agent.into()),
        None,
        None,
        None,
        None,
        None,
    )
}

fn classify_native_cli_home(
    path: &Utf8Path,
    relative_path: &str,
    remainder: &[&str],
    size: i64,
    provider: &str,
    agent: &str,
    root_kind: &str,
) -> StorageEntry {
    let name = remainder[remainder.len() - 1];
    if NATIVE_CLI_PROJECTED_ROOTS.contains(&remainder[0]) {
        return entry(
            path,
            relative_path,
            StorageClass::ProjectedConfig,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    if NATIVE_CLI_CACHE_ROOTS.contains(&remainder[0]) {
        return entry(
            path,
            relative_path,
            StorageClass::RebuildableCache,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            None,
        );
    }
    if NATIVE_CLI_SESSION_ROOTS.contains(&remainder[0])
        || name.ends_with(".db")
        || name.ends_with(".jsonl")
        || name.ends_with(".log")
        || name.ends_with(".sqlite")
        || name.ends_with(".sqlite-shm")
        || name.ends_with(".sqlite-wal")
    {
        return entry(
            path,
            relative_path,
            StorageClass::Session,
            size,
            root_kind,
            Some(provider.into()),
            Some(agent.into()),
            None,
            None,
            None,
            None,
            Some("native_cli_provider_state".into()),
        );
    }
    entry(
        path,
        relative_path,
        StorageClass::Session,
        size,
        root_kind,
        Some(provider.into()),
        Some(agent.into()),
        None,
        None,
        None,
        None,
        Some("native_cli_provider_owned_state".into()),
    )
}

fn claude_version_active(path: &Utf8Path, remainder: &[&str]) -> bool {
    if remainder.len() < 6 {
        return false;
    }
    let version = remainder[4];
    let home = provider_home_from_remainder(path, remainder);
    let home = match home {
        Some(h) => h,
        None => return false,
    };
    let link = home.join(".local/share/claude/versions").join(version);
    if !link.exists() {
        return false;
    }
    // Python checks whether the symlink at `.local/bin/claude` resolves into this version
    // directory. We approximate by checking the bin link target.
    let bin_link = home.join(".local/bin/claude");
    let target = match std::fs::read_link(&bin_link).ok() {
        Some(t) => {
            let resolved = if t.is_absolute() {
                t
            } else {
                bin_link.parent().unwrap_or(&home).join(t)
            };
            resolved.canonicalize().ok()
        }
        None => None,
    };
    match target {
        Some(t) => t.starts_with(&link),
        None => false,
    }
}

fn provider_home_from_remainder(path: &Utf8Path, remainder: &[&str]) -> Option<PathBuf> {
    let mut current = PathBuf::from(path.as_str());
    for _ in remainder {
        current = current.parent()?.to_path_buf();
    }
    Some(current)
}

#[allow(clippy::too_many_arguments)]
fn entry(
    path: &Utf8Path,
    relative_path: &str,
    storage_class: StorageClass,
    size: i64,
    root_kind: &str,
    provider: Option<String>,
    agent: Option<String>,
    active: Option<bool>,
    is_active_version: Option<bool>,
    reachable_from_current_symlink: Option<bool>,
    reclaimable: Option<bool>,
    reason: Option<String>,
) -> StorageEntry {
    StorageEntry {
        path: path.to_string(),
        relative_path: relative_path.to_string(),
        storage_class,
        size_bytes: size,
        provider,
        agent,
        active,
        is_active_version,
        reachable_from_current_symlink,
        reclaimable,
        reason,
        root_kind: root_kind.to_string(),
    }
}

fn summary_payload(
    layout: &PathLayout,
    entries: &[StorageEntry],
) -> serde_json::Map<String, serde_json::Value> {
    let mut by_class: HashMap<String, serde_json::Map<String, serde_json::Value>> = HashMap::new();
    let mut by_provider: HashMap<String, serde_json::Map<String, serde_json::Value>> =
        HashMap::new();
    let mut by_agent: HashMap<String, serde_json::Map<String, serde_json::Value>> = HashMap::new();
    let mut total_bytes: i64 = 0;

    for entry in entries {
        total_bytes += entry.size_bytes;
        accumulate(
            by_class
                .entry(entry.storage_class.as_str().to_string())
                .or_default(),
            entry.size_bytes,
        );
        if let Some(provider) = &entry.provider {
            accumulate(
                by_provider.entry(provider.clone()).or_default(),
                entry.size_bytes,
            );
        }
        if let Some(agent) = &entry.agent {
            accumulate(by_agent.entry(agent.clone()).or_default(), entry.size_bytes);
        }
    }

    let shared_cache_reason = shared_cache_disabled_reason(layout);
    let shared_cache_enabled = shared_cache_reason.is_none();

    let mut payload = serde_json::Map::new();
    payload.insert("schema_version".into(), SCHEMA_VERSION.into());
    payload.insert("generated_at".into(), utc_now().into());
    payload.insert("project".into(), layout.project_root.as_str().into());
    payload.insert("project_id".into(), layout.project_id().into());
    payload.insert(
        "runtime_root_kind".into(),
        layout.runtime_state_placement().root_kind.as_str().into(),
    );
    payload.insert(
        "runtime_state_root".into(),
        layout.runtime_state_root().as_str().into(),
    );
    payload.insert(
        "shared_cache_root".into(),
        shared_cache_root(layout, shared_cache_reason.as_deref().unwrap_or("")).into(),
    );
    payload.insert(
        "shared_cache_root_usable".into(),
        shared_cache_enabled.into(),
    );
    payload.insert(
        "shared_cache_status".into(),
        (if shared_cache_enabled {
            "enabled"
        } else {
            "disabled"
        })
        .into(),
    );
    payload.insert(
        "shared_cache_reason".into(),
        (if shared_cache_enabled {
            "enabled"
        } else {
            shared_cache_reason.as_deref().unwrap_or("")
        })
        .into(),
    );
    payload.insert("total_bytes".into(), total_bytes.into());
    payload.insert("total_count".into(), (entries.len() as i64).into());

    let sort_map = |map: HashMap<String, serde_json::Map<String, serde_json::Value>>| {
        let mut keys: Vec<String> = map.keys().cloned().collect();
        keys.sort();
        let mut sorted = serde_json::Map::new();
        for key in keys {
            sorted.insert(
                key.clone(),
                serde_json::Value::Object(map.get(&key).unwrap().clone()),
            );
        }
        serde_json::Value::Object(sorted)
    };

    payload.insert("by_class".into(), sort_map(by_class));
    payload.insert("by_provider".into(), sort_map(by_provider));
    payload.insert("by_agent".into(), sort_map(by_agent));

    let mut sorted_entries = entries.to_vec();
    sorted_entries.sort_by_key(|e| std::cmp::Reverse(e.size_bytes));
    payload.insert(
        "entries".into(),
        serde_json::Value::Array(
            sorted_entries
                .iter()
                .map(|e| serde_json::Value::Object(e.to_record()))
                .collect(),
        ),
    );

    payload
}

fn accumulate(bucket: &mut serde_json::Map<String, serde_json::Value>, size: i64) {
    let bytes = bucket.get("bytes").and_then(|v| v.as_i64()).unwrap_or(0) + size;
    let count = bucket.get("count").and_then(|v| v.as_i64()).unwrap_or(0) + 1;
    bucket.insert("bytes".into(), bytes.into());
    bucket.insert("count".into(), count.into());
}

fn shared_cache_root(layout: &PathLayout, disabled_reason: &str) -> Option<String> {
    if disabled_reason == "wsl_drvfs_requires_runtime_relocation" {
        return None;
    }
    Some(layout.shared_cache_dir().to_string())
}

fn shared_cache_disabled_reason(layout: &PathLayout) -> Option<String> {
    let placement = layout.runtime_state_placement();
    if placement.filesystem_hint.as_deref() == Some("wsl_drvfs")
        && !matches!(placement.root_kind, RootKind::Relocated)
    {
        Some("wsl_drvfs_requires_runtime_relocation".into())
    } else {
        None
    }
}

fn relative_display(
    _layout: &PathLayout,
    root: &Utf8Path,
    path: &Utf8Path,
    _root_kind: &str,
) -> String {
    path.strip_prefix(root).unwrap_or(path).to_string()
}

fn safe_size(path: &Utf8Path) -> i64 {
    fs::symlink_metadata(path)
        .map(|m| m.len() as i64)
        .unwrap_or(0)
}

fn scan_identity(path: &Utf8Path) -> ccb_storage::Result<(u64, u64)> {
    let stat = fs::symlink_metadata(path)?;
    Ok((stat.dev(), stat.ino()))
}

fn unsafe_symlink_reason(path: &Utf8Path, layout: &PathLayout) -> Option<String> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(m) => m,
        Err(_) => return Some("symlink_unreadable".into()),
    };
    if !metadata.file_type().is_symlink() {
        return None;
    }
    let target = match std::fs::read_link(path.as_str()) {
        Ok(t) => Utf8PathBuf::from_path_buf(t)
            .unwrap_or_else(|p| Utf8PathBuf::from(p.to_string_lossy().to_string())),
        Err(_) => return Some("symlink_target_missing".into()),
    };
    let resolved = {
        let to_resolve = if target.as_str().starts_with('/') {
            target.as_str().to_string()
        } else {
            path.parent()
                .map(|p| format!("{}/{}", p, target))
                .unwrap_or_else(|| target.to_string())
        };
        match std::fs::canonicalize(&to_resolve) {
            Ok(p) => p,
            Err(_) => return Some("symlink_target_missing".into()),
        }
    };
    let allowed_roots = [layout.ccb_dir(), layout.runtime_state_root().to_path_buf()];
    if allowed_roots
        .iter()
        .any(|root| resolved.starts_with(root.as_str()))
    {
        None
    } else {
        Some("symlink_out_of_bounds".into())
    }
}

fn is_allowed_provider_secret_symlink(path: &Utf8Path, layout: &PathLayout) -> bool {
    let relative = match path.strip_prefix(layout.ccb_dir()) {
        Ok(r) => r,
        Err(_) => return false,
    };
    let parts: Vec<&str> = relative.as_str().split('/').collect();
    parts.len() >= 7
        && parts[0] == "agents"
        && parts[2] == "provider-state"
        && parts[3] == "claude"
        && parts[4] == "home"
        && parts[5] == "Library"
        && parts[6] == "Keychains"
}

fn is_marked_projected_symlink(path: &Utf8Path) -> bool {
    let metadata = match fs::symlink_metadata(path) {
        Ok(m) => m,
        Err(_) => return false,
    };
    if !metadata.file_type().is_symlink() {
        return false;
    }
    let projection_path = format!("{}.ccb-projection.json", path);
    let data = match fs::read_to_string(&projection_path) {
        Ok(d) => d,
        Err(_) => return false,
    };
    let payload: serde_json::Value = match serde_json::from_str(&data) {
        Ok(v) => v,
        Err(_) => return false,
    };
    let obj = match payload.as_object() {
        Some(o) => o,
        None => return false,
    };
    if obj.get("record_type").and_then(|v| v.as_str()) != Some("ccb_projected_asset") {
        return false;
    }
    let valid_labels = [
        "claude-binary-versions",
        "claude-inherited-skills",
        "claude-inherited-commands",
        "codex-inherited-skills",
        "codex-inherited-commands",
        "codex-plugin-bundle",
        "droid-inherited-skills",
        "kimi-inherited-skills",
        "mimo-inherited-skills",
    ];
    let label = obj.get("label").and_then(|v| v.as_str()).unwrap_or("");
    let is_role_skill = label.starts_with("codex-role-skill:")
        || label.starts_with("claude-role-skill:")
        || label.starts_with("kimi-role-skill:");
    if !valid_labels.contains(&label) && !is_role_skill {
        return false;
    }
    let source = obj
        .get("source")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    if source.is_empty() {
        return false;
    }
    let source_resolved = match std::fs::canonicalize(expand_user_path(source)) {
        Ok(p) => p,
        Err(_) => return false,
    };
    let link_resolved = match std::fs::canonicalize(path.as_str()) {
        Ok(p) => p,
        Err(_) => return false,
    };
    source_resolved == link_resolved
}

fn expand_user_path(raw: &str) -> String {
    if let Some(rest) = raw.strip_prefix('~') {
        if let Ok(home) = std::env::var("HOME") {
            return home + rest;
        }
    }
    raw.to_string()
}

fn utc_now() -> String {
    chrono::Utc::now()
        .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
        .replace("+00:00", "Z")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_classify_provider_home_secret() {
        let path =
            Utf8Path::new("/repo/.ccb/agents/agent1/provider-state/unknownai/home/auth.json");
        let entry = classify_provider_home(
            path,
            "agents/agent1/provider-state/unknownai/home/auth.json",
            "UnknownAI",
            "agent1",
            &["auth.json"],
            2,
            "project",
        );
        assert_eq!(entry.storage_class, StorageClass::Secret);
        assert_eq!(entry.provider.as_deref(), Some("unknownai"));
        assert_eq!(entry.reason.as_deref(), Some("provider_secret"));
    }

    #[test]
    fn test_classify_provider_home_unknown() {
        let path =
            Utf8Path::new("/repo/.ccb/agents/agent1/provider-state/unknownai/home/notes.txt");
        let entry = classify_provider_home(
            path,
            "agents/agent1/provider-state/unknownai/home/notes.txt",
            "UnknownAI",
            "agent1",
            &["notes.txt"],
            5,
            "project",
        );
        assert_eq!(entry.storage_class, StorageClass::Unknown);
        assert_eq!(entry.provider.as_deref(), Some("unknownai"));
    }

    #[test]
    fn test_classify_kimi_home_projected() {
        let path = Utf8Path::new(
            "/repo/.ccb/agents/agent1/provider-state/kimi/home/inherited-skills/demo/SKILL.md",
        );
        let entry = classify_provider_home(
            path,
            "agents/agent1/provider-state/kimi/home/inherited-skills/demo/SKILL.md",
            "kimi",
            "agent1",
            &["inherited-skills", "demo", "SKILL.md"],
            10,
            "project",
        );
        assert_eq!(entry.storage_class, StorageClass::ProjectedConfig);
        assert_eq!(entry.provider.as_deref(), Some("kimi"));
    }

    #[test]
    fn test_classify_mimo_home() {
        let path =
            Utf8Path::new("/repo/.ccb/agents/agent1/provider-state/mimo/home/data/state.json");
        let entry = classify_provider_home(
            path,
            "agents/agent1/provider-state/mimo/home/data/state.json",
            "mimo",
            "agent1",
            &["data", "state.json"],
            10,
            "project",
        );
        assert_eq!(entry.storage_class, StorageClass::Session);
        assert_eq!(entry.provider.as_deref(), Some("mimo"));

        let config_path =
            Utf8Path::new("/repo/.ccb/agents/agent1/provider-state/mimo/home/mimocode.json");
        let config_entry = classify_provider_home(
            config_path,
            "agents/agent1/provider-state/mimo/home/mimocode.json",
            "mimo",
            "agent1",
            &["mimocode.json"],
            5,
            "project",
        );
        assert_eq!(config_entry.storage_class, StorageClass::ProjectedConfig);
    }

    #[test]
    fn test_classify_native_cli_home() {
        let path = Utf8Path::new(
            "/repo/.ccb/agents/agent1/provider-state/cursor/home/sessions/session.jsonl",
        );
        let entry = classify_provider_home(
            path,
            "agents/agent1/provider-state/cursor/home/sessions/session.jsonl",
            "cursor",
            "agent1",
            &["sessions", "session.jsonl"],
            10,
            "project",
        );
        assert_eq!(entry.storage_class, StorageClass::Session);
        assert_eq!(entry.reason.as_deref(), Some("native_cli_provider_state"));

        let cache_path =
            Utf8Path::new("/repo/.ccb/agents/agent1/provider-state/cursor/home/.cache/blob");
        let cache_entry = classify_provider_home(
            cache_path,
            "agents/agent1/provider-state/cursor/home/.cache/blob",
            "cursor",
            "agent1",
            &[".cache", "blob"],
            3,
            "project",
        );
        assert_eq!(cache_entry.storage_class, StorageClass::RebuildableCache);
    }

    #[test]
    fn test_runtime_skills_classified_as_projected_config() {
        let tmp = tempfile::TempDir::new().unwrap();
        let layout = PathLayout::new(Utf8PathBuf::from_path_buf(tmp.path().join("repo")).unwrap());
        let skills_dir = layout
            .runtime_state_root()
            .join("runtime/skills/agent1/codex");
        std::fs::create_dir_all(&skills_dir).unwrap();
        let skill_path = skills_dir.join("instruction.md");
        std::fs::write(&skill_path, "x").unwrap();

        let payload = summarize_storage(&layout).unwrap();
        let entries = payload.get("entries").unwrap().as_array().unwrap();
        let found = entries
            .iter()
            .find(|e| {
                e.get("relative_path").unwrap().as_str()
                    == Some("runtime/skills/agent1/codex/instruction.md")
            })
            .unwrap();
        assert_eq!(
            found.get("storage_class").unwrap().as_str(),
            Some("projected_config")
        );
        assert_eq!(
            found.get("reason").unwrap().as_str(),
            Some("provider_skill_instruction")
        );
    }
}
