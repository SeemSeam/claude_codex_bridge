//! CCB runtime environment helpers.
//!
//! Mirrors `lib/runtime_env/` from Python v7.5.2 and is the canonical home for
//! control-plane / user-session environment filtering and env parsing helpers.

pub mod control_plane;
pub mod env;
pub mod user_session;

/// Crate version.
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crate_smoke() {
        assert_eq!(version(), env!("CARGO_PKG_VERSION"));
    }
}
