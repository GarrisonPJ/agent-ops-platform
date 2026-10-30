use std::collections::HashSet;
use std::fmt;
use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};

use agentops_protocol::{EvaluationSpec, ExecutionMode};
use serde::Deserialize;

use crate::{TrustedCommand, DEFAULT_AGENT_REGISTRY_PATH};

const MAX_FILE_BYTES: usize = 65_536;
const MIN_AGENTS: usize = 1;
const MAX_AGENTS: usize = 32;
const MAX_ID_CHARS: usize = 64;
const MAX_NAME_CHARS: usize = 200;
const MAX_DESCRIPTION_CHARS: usize = 1_000;
const MAX_COMMAND_ARGS: usize = 32;
const MAX_ARG_CHARS: usize = 1_024;

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct AgentMetadata {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub description: String,
}

#[derive(Clone)]
struct RegistryAgent {
    pub metadata: AgentMetadata,
    command: TrustedCommand,
}

#[derive(Clone)]
pub struct AgentRegistry {
    agents: Vec<RegistryAgent>,
}

#[derive(Debug)]
pub enum RegistryError {
    Io(std::io::Error),
    Parse(toml::de::Error),
    Invalid(String),
    UnknownAgent(String),
}

impl fmt::Display for RegistryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(error) => write!(f, "failed to read agent registry: {error}"),
            Self::Parse(error) => write!(f, "invalid agent registry TOML: {error}"),
            Self::Invalid(message) => write!(f, "invalid agent registry: {message}"),
            Self::UnknownAgent(id) => write!(f, "agent '{id}' is not registered"),
        }
    }
}

impl std::error::Error for RegistryError {}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RegistryFile {
    agents: Vec<RegistryEntry>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RegistryEntry {
    id: String,
    name: String,
    kind: String,
    description: String,
    command: Vec<String>,
}

impl AgentRegistry {
    /// Load the operator-owned registry selected by `AGENTOPS_AGENT_REGISTRY`, or
    /// the explicitly packaged repository-relative default.
    pub fn from_env() -> Result<Self, RegistryError> {
        let path = std::env::var_os("AGENTOPS_AGENT_REGISTRY")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(DEFAULT_AGENT_REGISTRY_PATH));
        Self::from_path(path)
    }

    pub fn from_path(path: impl AsRef<Path>) -> Result<Self, RegistryError> {
        Self::from_reader(File::open(path).map_err(RegistryError::Io)?)
    }

    fn from_reader(reader: impl Read) -> Result<Self, RegistryError> {
        let mut bytes = Vec::new();
        reader
            .take((MAX_FILE_BYTES + 1) as u64)
            .read_to_end(&mut bytes)
            .map_err(RegistryError::Io)?;
        if bytes.len() > MAX_FILE_BYTES {
            return Err(RegistryError::Invalid(format!(
                "file must be at most {MAX_FILE_BYTES} bytes"
            )));
        }
        Self::from_toml(
            std::str::from_utf8(&bytes)
                .map_err(|_| RegistryError::Invalid("file must be valid UTF-8 TOML".into()))?,
        )
    }

    pub fn from_toml(input: &str) -> Result<Self, RegistryError> {
        if input.len() > MAX_FILE_BYTES {
            return Err(RegistryError::Invalid(format!(
                "file must be at most {MAX_FILE_BYTES} bytes"
            )));
        }
        let parsed: RegistryFile = toml::from_str(input).map_err(RegistryError::Parse)?;
        if !(MIN_AGENTS..=MAX_AGENTS).contains(&parsed.agents.len()) {
            return Err(RegistryError::Invalid(format!(
                "agents must contain between {MIN_AGENTS} and {MAX_AGENTS} entries"
            )));
        }
        let mut ids = HashSet::with_capacity(parsed.agents.len());
        let mut agents = Vec::with_capacity(parsed.agents.len());
        for entry in parsed.agents {
            validate_entry(&entry)?;
            if !ids.insert(entry.id.clone()) {
                return Err(RegistryError::Invalid(format!(
                    "duplicate agent id '{}'",
                    entry.id
                )));
            }
            let mut args = entry.command;
            let program = args.remove(0);
            agents.push(RegistryAgent {
                metadata: AgentMetadata {
                    id: entry.id,
                    name: entry.name,
                    kind: entry.kind,
                    description: entry.description,
                },
                command: TrustedCommand::new(program, args),
            });
        }
        Ok(Self { agents })
    }

    pub fn metadata(&self) -> impl Iterator<Item = &AgentMetadata> {
        self.agents.iter().map(|entry| &entry.metadata)
    }

    /// Resolve an optional spec selection. Omission deliberately follows the
    /// existing execution-mode compatibility default; an explicit id never does.
    pub fn resolve_spec(&self, spec: &EvaluationSpec) -> Result<TrustedCommand, RegistryError> {
        self.resolve(spec.agent_id.as_deref(), &spec.execution_mode)
    }

    pub fn resolve(
        &self,
        agent_id: Option<&str>,
        execution_mode: &ExecutionMode,
    ) -> Result<TrustedCommand, RegistryError> {
        let selected = agent_id.unwrap_or(match execution_mode {
            ExecutionMode::Fixture => "fixture-agent.v1",
            ExecutionMode::Provider => "reference-openai-compatible.v1",
        });
        self.agents
            .iter()
            .find(|entry| entry.metadata.id == selected)
            .map(|entry| entry.command.clone())
            .ok_or_else(|| RegistryError::UnknownAgent(selected.to_string()))
    }
}

fn validate_entry(entry: &RegistryEntry) -> Result<(), RegistryError> {
    if !is_versioned_id(&entry.id) {
        return Err(RegistryError::Invalid(format!(
            "id '{}' is not a valid versioned name",
            entry.id
        )));
    }
    validate_text("name", &entry.name, MAX_NAME_CHARS)?;
    validate_text("description", &entry.description, MAX_DESCRIPTION_CHARS)?;
    if !matches!(entry.kind.as_str(), "fixture" | "reference") {
        return Err(RegistryError::Invalid(format!(
            "kind '{}' is not supported in H1",
            entry.kind
        )));
    }
    if !(1..=MAX_COMMAND_ARGS).contains(&entry.command.len()) {
        return Err(RegistryError::Invalid(format!(
            "command must contain between 1 and {MAX_COMMAND_ARGS} arguments"
        )));
    }
    for argument in &entry.command {
        if argument.is_empty()
            || argument.chars().count() > MAX_ARG_CHARS
            || argument.contains('\0')
        {
            return Err(RegistryError::Invalid(
                "command arguments must contain 1 to 1024 characters and no NUL".into(),
            ));
        }
    }
    if entry.command[0].trim().is_empty() {
        return Err(RegistryError::Invalid(
            "command program must not be whitespace-only".into(),
        ));
    }
    Ok(())
}

fn validate_text(field: &str, value: &str, max: usize) -> Result<(), RegistryError> {
    if value.is_empty() || value.chars().count() > max || value.contains('\0') {
        return Err(RegistryError::Invalid(format!(
            "{field} must contain 1 to {max} characters and no NUL"
        )));
    }
    Ok(())
}

fn is_versioned_id(value: &str) -> bool {
    let Some((base, version)) = value.rsplit_once(".v") else {
        return false;
    };
    if value.is_empty() || value.len() > MAX_ID_CHARS || base.len() < 2 || version.is_empty() {
        return false;
    }
    if !version.chars().all(|ch| ch.is_ascii_digit()) {
        return false;
    }
    let bytes = base.as_bytes();
    (bytes[0].is_ascii_lowercase() || bytes[0].is_ascii_digit())
        && bytes[1..]
            .iter()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'-')
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIXTURE: &str = r#"
[[agents]]
id = "fixture-agent.v1"
name = "Fixture"
kind = "fixture"
description = "Deterministic fixture agent."
command = ["python", "-m", "app.fixture_agent", "quoted arg; $HOME"]

[[agents]]
id = "reference-openai-compatible.v1"
name = "Reference"
kind = "reference"
description = "Reference provider agent."
command = ["python", "-m", "app.reference_agent"]
"#;

    #[test]
    fn strict_parse_and_metadata_never_exposes_command() {
        let registry = AgentRegistry::from_toml(FIXTURE).unwrap();
        let metadata: Vec<_> = registry.metadata().collect();
        assert_eq!(metadata[0].id, "fixture-agent.v1");
        assert_eq!(metadata[0].kind, "fixture");
        assert!(format!("{metadata:?}").find("quoted arg").is_none());
        assert!(AgentRegistry::from_toml(
            &FIXTURE.replace("[[agents]]", "extra = true\n[[agents]]")
        )
        .is_err());
    }

    #[test]
    fn duplicate_unknown_and_bounds_fail_closed() {
        let duplicate = FIXTURE.replacen("reference-openai-compatible.v1", "fixture-agent.v1", 1);
        assert!(AgentRegistry::from_toml(&duplicate).is_err());
        assert!(AgentRegistry::from_toml(
            &FIXTURE.replace("kind = \"fixture\"", "kind = \"external\"")
        )
        .is_err());
        assert!(AgentRegistry::from_toml(
            &FIXTURE.replace("id = \"fixture-agent.v1\"", "id = \"BAD\"")
        )
        .is_err());
        assert!(AgentRegistry::from_toml(&FIXTURE.replace(
            "description = \"Deterministic fixture agent.\"",
            "description = \"\""
        ))
        .is_err());
        assert!(AgentRegistry::from_toml(&FIXTURE.replace(
            "command = [\"python\", \"-m\", \"app.fixture_agent\", \"quoted arg; $HOME\"]",
            "command = []"
        ))
        .is_err());
    }

    #[test]
    fn registry_identifiers_match_python_ascii_grammar() {
        for id in [
            "ab.v1".into(),
            "0-.v0".into(),
            format!("{}.v1", "a".repeat(61)),
        ] {
            assert!(
                AgentRegistry::from_toml(&FIXTURE.replacen("fixture-agent.v1", &id, 1)).is_ok(),
                "{id}"
            );
        }
        for id in [
            "a.v1".into(),
            "ab.v".into(),
            "ab.v-1".into(),
            "éx.v1".into(),
            " ab.v1".into(),
            "ab.v1 ".into(),
            format!("{}.v1", "a".repeat(62)),
        ] {
            assert!(
                AgentRegistry::from_toml(&FIXTURE.replacen("fixture-agent.v1", &id, 1)).is_err(),
                "{id}"
            );
        }
    }

    #[test]
    fn metadata_and_argv_bounds_match_python_unicode_and_nul_rules() {
        for (field, original, maximum) in [
            ("name", "Fixture", MAX_NAME_CHARS),
            (
                "description",
                "Deterministic fixture agent.",
                MAX_DESCRIPTION_CHARS,
            ),
        ] {
            let replace = format!("{field} = \"{original}\"");
            let valid = format!("{field} = \"{}\"", "界".repeat(maximum));
            assert!(AgentRegistry::from_toml(&FIXTURE.replacen(&replace, &valid, 1)).is_ok());
            for value in [String::new(), "界".repeat(maximum + 1), "\\u0000".into()] {
                let invalid = format!("{field} = \"{value}\"");
                assert!(
                    AgentRegistry::from_toml(&FIXTURE.replacen(&replace, &invalid, 1)).is_err()
                );
            }
        }
        let replace =
            "command = [\"python\", \"-m\", \"app.fixture_agent\", \"quoted arg; $HOME\"]";
        let valid = format!("command = [\"python\", \"{}\"]", "界".repeat(MAX_ARG_CHARS));
        assert!(AgentRegistry::from_toml(&FIXTURE.replacen(replace, &valid, 1)).is_ok());
        for invalid in [
            "command = []".into(),
            "command = [\" \" ]".into(),
            "command = [\"python\", \"\"]".into(),
            "command = [\"python\", \"\\u0000\"]".into(),
            format!("command = [\"{}\"]", "x".repeat(MAX_ARG_CHARS + 1)),
            format!(
                "command = [{}]",
                vec!["\"python\""; MAX_COMMAND_ARGS + 1].join(",")
            ),
        ] {
            assert!(AgentRegistry::from_toml(&FIXTURE.replacen(replace, &invalid, 1)).is_err());
        }
    }

    #[test]
    fn file_reader_is_bounded_even_when_stream_exceeds_limit() {
        let mut exact = FIXTURE.as_bytes().to_vec();
        exact.resize(MAX_FILE_BYTES, b' ');
        assert!(AgentRegistry::from_reader(std::io::Cursor::new(&exact)).is_ok());
        exact.extend_from_slice(&[b' '; MAX_FILE_BYTES]);
        let mut reader = std::io::Cursor::new(exact);
        assert!(AgentRegistry::from_reader(&mut reader).is_err());
        assert_eq!(reader.position(), (MAX_FILE_BYTES + 1) as u64);
    }

    #[test]
    fn resolver_preserves_argv_and_applies_mode_fallback() {
        let registry = AgentRegistry::from_toml(FIXTURE).unwrap();
        let command = registry.resolve(None, &ExecutionMode::Fixture).unwrap();
        assert_eq!(command.program, "python");
        assert_eq!(
            command.args,
            ["-m", "app.fixture_agent", "quoted arg; $HOME"]
        );
        assert_eq!(
            registry
                .resolve(None, &ExecutionMode::Provider)
                .unwrap()
                .args,
            ["-m", "app.reference_agent"]
        );
        assert_eq!(
            registry
                .resolve(Some("fixture-agent.v1"), &ExecutionMode::Provider)
                .unwrap()
                .args,
            ["-m", "app.fixture_agent", "quoted arg; $HOME"]
        );
        assert!(matches!(
            registry.resolve(Some("missing.v1"), &ExecutionMode::Fixture),
            Err(RegistryError::UnknownAgent(_))
        ));
    }
}
