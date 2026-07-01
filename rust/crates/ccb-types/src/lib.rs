//! Shared CCB types and lightweight helpers.
//!
//! Canonical implementations for environment filtering, UI text, and other
//! cross-cutting concerns now live in dedicated crates and are re-exported
//! here for backward compatibility.

pub use ccb_runtime_env::{control_plane, env, user_session};
pub mod error;
pub mod ui;
