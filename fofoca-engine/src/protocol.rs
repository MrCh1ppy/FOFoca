use std::{collections::BTreeMap, fmt};

use serde::{
    Deserialize, Deserializer, Serialize,
    de::{self, MapAccess, SeqAccess, Visitor},
};
use serde_json::{Value, json};
use snafu::Snafu;

use crate::session::SessionError;

#[derive(Debug, Snafu)]
pub(crate) enum ProtocolError {
    #[snafu(display("Invalid JSON: {source}"))]
    Json { source: serde_json::Error },
    #[snafu(display("Duplicate JSON object key: {source}"))]
    Duplicate { source: serde_json::Error },
    #[snafu(display("Invalid request schema: {source}"))]
    Schema { source: serde_json::Error },
    #[snafu(display("request_id must be nonempty and params must be an object"))]
    Envelope,
    #[snafu(display("Unknown command"))]
    UnknownCommand,
}

impl ProtocolError {
    fn code(&self) -> &'static str {
        match self {
            Self::Json { .. } => "invalid_json",
            Self::Duplicate { .. } => "duplicate_key",
            Self::Schema { .. } | Self::Envelope => "invalid_request",
            Self::UnknownCommand => "unknown_command",
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    request_id: String,
    command: String,
    params: Value,
}

// Wire shapes only: semantic date/code/decimal validation belongs to later phases.
#[allow(dead_code)]
#[derive(Deserialize)]
#[serde(
    tag = "command",
    content = "params",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub(crate) enum Command {
    Init {
        start_date: String,
        end_date: String,
        initial_cash: String,
    },
    Account {},
    SetOrders {
        orders: Vec<Order>,
    },
    AdvanceDay {
        market: Market,
    },
    Report {},
    Quit {},
}

#[allow(dead_code)]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Order {
    code: String,
    side: Side,
    shares: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum Side {
    Buy,
    Sell,
}

#[allow(dead_code)]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Market {
    date: String,
    unit_nav: BTreeMap<String, Option<String>>,
}

/// Recursive deserialization rejects keys before serde_json can overwrite them.
struct UniqueValue(Value);

impl<'de> Deserialize<'de> for UniqueValue {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct UniqueVisitor;
        impl<'de> Visitor<'de> for UniqueVisitor {
            type Value = UniqueValue;
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("a JSON value with unique object keys")
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
                let mut values = serde_json::Map::new();
                while let Some(key) = map.next_key::<String>()? {
                    if values.contains_key(&key) {
                        return Err(de::Error::custom("duplicate object key"));
                    }
                    values.insert(key, map.next_value::<UniqueValue>()?.0);
                }
                Ok(UniqueValue(Value::Object(values)))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Self::Value, A::Error> {
                let mut values = Vec::new();
                while let Some(value) = seq.next_element::<UniqueValue>()? {
                    values.push(value.0);
                }
                Ok(UniqueValue(Value::Array(values)))
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> Result<Self::Value, E> {
                Ok(UniqueValue(v.into()))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> Result<Self::Value, E> {
                Ok(UniqueValue(v.into()))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> Result<Self::Value, E> {
                Ok(UniqueValue(v.into()))
            }
            fn visit_f64<E: de::Error>(self, v: f64) -> Result<Self::Value, E> {
                Ok(UniqueValue(v.into()))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<Self::Value, E> {
                Ok(UniqueValue(v.into()))
            }
            fn visit_unit<E: de::Error>(self) -> Result<Self::Value, E> {
                Ok(UniqueValue(Value::Null))
            }
        }
        deserializer.deserialize_any(UniqueVisitor)
    }
}

#[derive(Serialize)]
pub(crate) struct Response {
    request_id: Option<String>,
    ok: bool,
    data: Value,
    error: Value,
    state: Value,
}

impl Response {
    pub(crate) fn session_failure(raw: &Value, line: &str, error: SessionError) -> Self {
        let code = match error {
            SessionError::NotImplemented => "not_implemented",
            SessionError::NotInitialized => "not_initialized",
        };
        Self::failure(raw, line, code, error.to_string())
    }

    pub(crate) fn success(raw: &Value) -> Self {
        Self {
            request_id: request_id(raw),
            ok: true,
            data: json!({}),
            error: Value::Null,
            state: Value::Null,
        }
    }

    pub(crate) fn failure(raw: &Value, line: &str, code: &str, message: String) -> Self {
        let submitted_orders = if raw.get("command").and_then(Value::as_str) == Some("set_orders") {
            raw.pointer("/params/orders")
                .filter(|v| v.is_array())
                .cloned()
                .unwrap_or(Value::Null)
        } else {
            Value::Null
        };
        Self {
            request_id: request_id(raw),
            ok: false,
            data: Value::Null,
            state: Value::Null,
            error: json!({"code":code,"message":message,"raw_request":raw,"raw_line":line,
                "submitted_orders":submitted_orders,"net_operations":null,"skips":null,
                "issues":[{"code":code,"path":"","message":message,"code_context":null,"details":{}}]}),
        }
    }
}

fn request_id(raw: &Value) -> Option<String> {
    raw.get("request_id")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
}

pub(crate) fn parse(line: &str) -> Result<(Value, Command), Box<Response>> {
    let raw = serde_json::from_str::<UniqueValue>(line)
        .map_err(|source| {
            let error = if source.to_string().starts_with("duplicate object key") {
                ProtocolError::Duplicate { source }
            } else {
                ProtocolError::Json { source }
            };
            Box::new(Response::failure(
                &Value::Null,
                line,
                error.code(),
                error.to_string(),
            ))
        })?
        .0;
    let validate = || -> Result<Command, ProtocolError> {
        let envelope: Envelope = serde_json::from_value(raw.clone())
            .map_err(|source| ProtocolError::Schema { source })?;
        if envelope.request_id.is_empty() || !envelope.params.is_object() {
            return Err(ProtocolError::Envelope);
        }
        if ![
            "init",
            "account",
            "set_orders",
            "advance_day",
            "report",
            "quit",
        ]
        .contains(&envelope.command.as_str())
        {
            return Err(ProtocolError::UnknownCommand);
        }
        serde_json::from_value(json!({"command":envelope.command,"params":envelope.params}))
            .map_err(|source| ProtocolError::Schema { source })
    };
    match validate() {
        Ok(command) => Ok((raw, command)),
        Err(error) => Err(Box::new(Response::failure(
            &raw,
            line,
            error.code(),
            error.to_string(),
        ))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duplicate_keys_are_recursive_and_ids_are_not_recovered() {
        for line in [
            r#"{"request_id":"x","request_id":"y"}"#,
            r#"{"request_id":"x","params":{"a":[{"b":1,"b":2}]}}"#,
        ] {
            let response = parse(line).err().unwrap();
            assert_eq!(response.error["code"], "duplicate_key");
            assert_eq!(response.request_id, None);
            assert_eq!(response.error["raw_request"], Value::Null);
        }
    }

    #[test]
    fn recover_id_only_after_complete_unique_json() {
        for (line, id) in [
            (r#"{"request_id":"x","command":1}"#, Some("x")),
            (r#"{"request_id":"x"} trailing"#, None),
            (r#"{"request_id":""}"#, None),
        ] {
            assert_eq!(parse(line).err().unwrap().request_id.as_deref(), id);
        }
    }
}
