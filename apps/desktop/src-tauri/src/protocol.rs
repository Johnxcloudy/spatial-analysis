use serde::Serialize;
use serde_json::{json, Value};

#[cfg(not(test))]
pub const MAX_REQUEST_BYTES: usize = 1024 * 1024;
#[cfg(not(test))]
pub const MAX_RESPONSE_BYTES: usize = 8 * 1024 * 1024;

#[derive(Debug, Serialize)]
pub struct EngineError {
    pub code: i64,
    pub message: String,
    pub data: Value,
}

impl EngineError {
    pub fn local(kind: &str, message: impl Into<String>) -> Self {
        Self {
            code: -32000,
            message: message.into(),
            data: json!({"kind":kind}),
        }
    }
}

pub fn validate_request(method: &str, params: &Value) -> Result<(), EngineError> {
    if !matches!(
        method,
        "runtime.info"
            | "project.create"
            | "project.open"
            | "project.save"
            | "project.close"
            | "diagnostics.run"
            | "source.inspect"
            | "workspace.get"
            | "vector.import"
            | "vector.export"
            | "task.get"
            | "task.cancel"
            | "layer.update"
            | "layer.reorder"
            | "layer.remove"
            | "vector.page"
            | "vector.viewport"
            | "vector.feature"
    ) {
        return Err(EngineError {
            code: -32601,
            message: "不支持的本地引擎命令。".into(),
            data: json!({"kind":"METHOD_NOT_FOUND"}),
        });
    }
    if !params.is_object() {
        return Err(EngineError {
            code: -32602,
            message: "命令参数必须是对象。".into(),
            data: json!({"kind":"INVALID_PARAMS"}),
        });
    }
    Ok(())
}

pub fn decode_response(payload: Value, id: u64) -> Result<Value, EngineError> {
    let malformed = || {
        EngineError::local(
            "ENGINE_PROTOCOL",
            "GIS 引擎返回了不匹配的协议响应，请重试并重新打开项目。",
        )
    };
    let object = payload.as_object().ok_or_else(malformed)?;
    if payload["jsonrpc"] != "2.0"
        || payload["id"].as_u64() != Some(id)
        || object.contains_key("result") == object.contains_key("error")
    {
        return Err(malformed());
    }
    if let Some(error) = object.get("error") {
        let code = error["code"].as_i64().ok_or_else(malformed)?;
        let message = error["message"].as_str().ok_or_else(malformed)?;
        return Err(EngineError {
            code,
            message: message.to_owned(),
            data: error
                .get("data")
                .cloned()
                .unwrap_or_else(|| json!({"kind":"ENGINE_ERROR"})),
        });
    }
    Ok(payload["result"].clone())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_unlisted_method_and_non_object_params() {
        assert!(validate_request("system.exec", &json!({})).is_err());
        assert!(validate_request("project.open", &json!([])).is_err());
        assert!(validate_request("runtime.info", &json!({})).is_ok());
    }

    #[test]
    fn unwraps_matching_response() {
        assert_eq!(
            decode_response(json!({"jsonrpc":"2.0","id":7,"result":{"ok":true}}), 7).unwrap(),
            json!({"ok":true})
        );
    }

    #[test]
    fn rejects_wrong_id_and_malformed_envelopes() {
        assert!(decode_response(json!({"jsonrpc":"2.0","id":6,"result":true}), 7).is_err());
        assert!(decode_response(json!({"jsonrpc":"2.0","id":7}), 7).is_err());
        assert!(decode_response(
            json!({"jsonrpc":"2.0","id":7,"result":true,"error":{"code":-1,"message":"bad"}}),
            7
        )
        .is_err());
    }

    #[test]
    fn preserves_engine_domain_error() {
        let error = decode_response(json!({"jsonrpc":"2.0","id":7,"error":{"code":-32000,"message":"Project locked","data":{"kind":"PROJECT_LOCKED"}}}), 7).unwrap_err();
        assert_eq!(error.code, -32000);
        assert_eq!(error.data["kind"], "PROJECT_LOCKED");
    }
}
