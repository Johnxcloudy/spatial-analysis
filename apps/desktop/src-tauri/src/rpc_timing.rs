use crate::protocol::{validate_request, EngineError};
use serde_json::{json, Value};
use tokio::time::Instant;

pub struct RpcTiming {
    pub started: Instant,
    pub request_id: Option<u64>,
    pub queue_ms: Option<f64>,
    pub startup_ms: Option<f64>,
    pub exchange_ms: Option<f64>,
    pub finish_ms: Option<f64>,
    enabled: bool,
    unix_start_ms: u128,
}

impl RpcTiming {
    pub fn new(enabled: bool) -> Self {
        Self {
            started: Instant::now(),
            request_id: None,
            queue_ms: None,
            startup_ms: None,
            exchange_ms: None,
            finish_ms: None,
            enabled,
            unix_start_ms: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis(),
        }
    }

    pub fn record(&self, method: &str, result: &Result<Value, EngineError>) -> Option<Value> {
        if !self.enabled {
            return None;
        }
        let method = if validate_request(method, &json!({})).is_ok() {
            method
        } else {
            "<invalid>"
        };
        let error_kind = result.as_ref().err().map(|error| {
            error.data["kind"]
                .as_str()
                .filter(|kind| {
                    !kind.is_empty()
                        && kind.len() <= 64
                        && kind
                            .bytes()
                            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
                })
                .unwrap_or("unknown")
        });
        Some(json!({
            "version": 1,
            "hostPid": std::process::id(),
            "requestId": self.request_id,
            "method": method,
            "startedAtUnixMs": self.unix_start_ms,
            "queueMs": self.queue_ms,
            "startupMs": self.startup_ms,
            "exchangeMs": self.exchange_ms,
            "finishMs": self.finish_ms,
            "totalMs": self.started.elapsed().as_secs_f64() * 1000.0,
            "outcome": if result.is_ok() { "success" } else { "error" },
            "errorKind": error_kind,
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn disabled_trace_never_serializes_a_response() {
        let trace = RpcTiming::new(false);
        assert!(trace
            .record("vector.page", &Ok(json!({"secret": "private"})))
            .is_none());
    }

    #[test]
    fn trace_retains_phase_identity_without_response_or_error_detail() {
        let mut trace = RpcTiming::new(true);
        trace.request_id = Some(17);
        trace.queue_ms = Some(120.0);
        trace.exchange_ms = Some(12.0);
        let success = trace
            .record("vector.page", &Ok(json!({"private": "not-for-log"})))
            .unwrap();
        assert_eq!(success["version"], 1);
        assert_eq!(success["requestId"], 17);
        assert_eq!(success["queueMs"], 120.0);
        assert_eq!(success["exchangeMs"], 12.0);
        assert_eq!(success["outcome"], "success");
        assert!(success["startedAtUnixMs"].as_u64().unwrap() > 0);
        assert!(!success.to_string().contains("not-for-log"));
        let failure = trace
            .record(
                "vector.page",
                &Err(EngineError {
                    code: -32000,
                    message: "private-error-text".into(),
                    data: json!({"kind": "query_timeout", "detail": "private-project-path"}),
                }),
            )
            .unwrap();
        assert_eq!(failure["outcome"], "error");
        assert_eq!(failure["errorKind"], "query_timeout");
        assert!(!failure.to_string().contains("private"));
    }

    #[test]
    fn trace_rejects_arbitrary_method_and_error_text() {
        let trace = RpcTiming::new(true);
        let record = trace
            .record(
                "private/path\nforged",
                &Err(EngineError::local("private/path\nforged", "private")),
            )
            .unwrap();
        assert_eq!(record["method"], "<invalid>");
        assert_eq!(record["errorKind"], "unknown");
        assert!(!record.to_string().contains("private"));
    }
}
