//! Environment variable parsing helpers.
//!
//! Mirrors `runtime_env/__init__.py` from Python v7.5.2.

use std::env;

/// Parse a boolean value from the environment.
///
/// Returns `default` when the variable is missing, empty, or not one of the
/// recognized truthy/falsy strings.
pub fn env_bool(name: &str, default: bool) -> bool {
    match env::var(name) {
        Ok(val) => {
            if val.is_empty() {
                return default;
            }
            let v = val.trim().to_lowercase();
            match v.as_str() {
                "1" | "true" | "yes" | "on" => true,
                "0" | "false" | "no" | "off" => false,
                _ => default,
            }
        }
        Err(_) => default,
    }
}

/// Parse an integer value from the environment.
///
/// Returns `default` when the variable is missing, empty, or not a valid
/// integer.
pub fn env_int(name: &str, default: i64) -> i64 {
    env::var(name)
        .ok()
        .filter(|v| !v.is_empty())
        .and_then(|v| v.trim().parse().ok())
        .unwrap_or(default)
}

/// Parse a floating-point value from the environment.
///
/// Returns `default` when the variable is missing, empty, or not a valid
/// float.
pub fn env_float(name: &str, default: f64) -> f64 {
    env::var(name)
        .ok()
        .filter(|v| !v.is_empty())
        .and_then(|v| v.trim().parse().ok())
        .unwrap_or(default)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_env_bool_default() {
        assert!(!env_bool("CCB_NONEXISTENT_VAR_XYZ", false));
        assert!(env_bool("CCB_NONEXISTENT_VAR_XYZ", true));
    }

    #[test]
    fn test_env_bool_truthy_and_falsy() {
        for v in ["1", "true", "yes", "on", " TRUE ", "Yes"] {
            env::set_var("X", v);
            assert!(env_bool("X", false));
        }

        for v in ["0", "false", "no", "off", " 0 ", "False"] {
            env::set_var("X", v);
            assert!(!env_bool("X", true));
        }

        env::set_var("X", "maybe");
        assert!(env_bool("X", true));
        assert!(!env_bool("X", false));
    }

    #[test]
    fn test_env_bool_empty_string_uses_default() {
        env::set_var("X", "");
        assert!(env_bool("X", true));
        assert!(!env_bool("X", false));
    }

    #[test]
    fn test_env_int_parsing() {
        env::remove_var("X");
        assert_eq!(env_int("X", 7), 7);

        env::set_var("X", " 42 ");
        assert_eq!(env_int("X", 7), 42);

        env::set_var("X", "bad");
        assert_eq!(env_int("X", 7), 7);
    }

    #[test]
    fn test_env_float_parsing() {
        env::remove_var("X");
        assert!((env_float("X", 1.5) - 1.5).abs() < f64::EPSILON);

        env::set_var("X", " 2.75 ");
        assert!((env_float("X", 1.5) - 2.75).abs() < f64::EPSILON);

        env::set_var("X", "bad");
        assert!((env_float("X", 1.5) - 1.5).abs() < f64::EPSILON);
    }
}
