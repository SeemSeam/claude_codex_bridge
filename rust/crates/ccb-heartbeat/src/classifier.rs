//! Mirrors Python `lib/maintenance_heartbeat/classifier.py`.
//!
//! The classification logic originally planned for this module now lives in
//! `crate::maintenance`. This module re-exports the public classifier entry
//! points so any historical imports continue to work.

pub use crate::maintenance::{
    evaluate_project_view, evaluate_ps_summary, MaintenanceHeartbeatEvaluation, HEALTH_CONCERN,
    HEALTH_FAILING, HEALTH_HEALTHY, HEALTH_UNKNOWN, RECOMMENDED_ACTION_ASSESS_LATER,
    RECOMMENDED_ACTION_NONE,
};
