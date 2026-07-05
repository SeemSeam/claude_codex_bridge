//! CCB UI text / help strings.
//!
//! Mirrors `lib/ui_text/` from Python v7.5.2. This crate is the canonical home
//! for status labels and translated user-facing messages.

pub mod i18n;
pub mod text;

/// Re-exports matching Python `ui_text.__all__`.
pub use i18n::{detect_language, get_lang, set_lang, t, Language, MESSAGES};

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

    #[test]
    fn status_constants_present() {
        assert_eq!(text::STATUS_BLOCKED, "blocked");
        assert_eq!(text::STATUS_READ_ONLY, "read-only");
    }

    #[test]
    #[serial_test::serial]
    fn english_message_lookup() {
        set_lang(Language::English);
        let rendered = t("sending_to", &[("provider", "claude")]);
        assert!(rendered.contains("claude"));
        assert!(rendered.contains("Sending"));
    }

    #[test]
    #[serial_test::serial]
    fn fallback_returns_key_for_unknown_message() {
        set_lang(Language::English);
        assert_eq!(t("__unknown_key__", &[]), "__unknown_key__");
    }

    #[test]
    fn messages_static_exported() {
        assert!(MESSAGES.contains_key(&Language::English));
        assert!(MESSAGES.contains_key(&Language::Chinese));
    }
}
