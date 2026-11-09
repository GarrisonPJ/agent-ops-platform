//! Bounded capture of the Agent's final stderr line (ADR-0009 H5-era amendment).
//!
//! Agent output is untrusted. The Runner keeps counting every stderr byte
//! against `max_output_bytes` (ADR-0002 output bound) and additionally keeps at
//! most one 64-byte candidate line. After the Agent exits, that line is
//! reported only when it equals an allowlisted failure code exactly; raw
//! stderr text is never stored or uploaded.

use agentops_protocol::{AGENT_ERROR_CODES, MAX_AGENT_ERROR_LINE_BYTES};

#[derive(Debug, Default)]
pub(crate) struct AgentStderrTail {
    current: Vec<u8>,
    current_too_long: bool,
    /// The last COMPLETE line; `None` when there is none or it was too long.
    last_line: Option<Vec<u8>>,
}

impl AgentStderrTail {
    pub(crate) fn observe(&mut self, bytes: &[u8]) {
        for &byte in bytes {
            if byte == b'\n' {
                // A later complete line, even an empty or oversized one,
                // supersedes an earlier code: only the final line counts.
                let line = std::mem::take(&mut self.current);
                self.last_line = if self.current_too_long {
                    None
                } else {
                    Some(line)
                };
                self.current_too_long = false;
            } else if !self.current_too_long {
                if self.current.len() < MAX_AGENT_ERROR_LINE_BYTES {
                    self.current.push(byte);
                } else {
                    // Never buffer an unbounded line: drop it entirely.
                    self.current_too_long = true;
                    self.current = Vec::new();
                }
            }
        }
    }

    /// The allowlisted code on the last complete line, if any. An unterminated
    /// trailing fragment is ignored.
    pub(crate) fn reported_code(&self) -> Option<&'static str> {
        let line = self.last_line.as_deref()?;
        let line = line.strip_suffix(b"\r").unwrap_or(line);
        AGENT_ERROR_CODES
            .iter()
            .copied()
            .find(|code| code.as_bytes() == line)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tail_of(chunks: &[&[u8]]) -> AgentStderrTail {
        let mut tail = AgentStderrTail::default();
        for chunk in chunks {
            tail.observe(chunk);
        }
        tail
    }

    #[test]
    fn allowlisted_last_line_is_reported() {
        assert_eq!(
            tail_of(&[b"PI_PROVIDER_ERROR\n"]).reported_code(),
            Some("PI_PROVIDER_ERROR")
        );
        assert_eq!(
            tail_of(&[b"noise\nPROVIDER_TIMEOUT\r\n"]).reported_code(),
            Some("PROVIDER_TIMEOUT")
        );
    }

    #[test]
    fn code_survives_arbitrary_chunk_boundaries() {
        let tail = tail_of(&[b"AGENT_", b"TIME", b"OUT", b"\n"]);
        assert_eq!(tail.reported_code(), Some("AGENT_TIMEOUT"));
    }

    #[test]
    fn every_allowlisted_code_round_trips() {
        for code in AGENT_ERROR_CODES {
            let line = format!("{code}\n");
            assert_eq!(
                tail_of(&[line.as_bytes()]).reported_code(),
                Some(*code),
                "{code}"
            );
        }
    }

    #[test]
    fn non_allowlisted_text_is_not_reported() {
        for line in [
            &b"provider_timeout\n"[..],
            b"PROVIDER_TIMEOUT \n",
            b" PROVIDER_TIMEOUT\n",
            b"PROVIDER_TIMEOUT: http://secret\n",
            b"PROVIDER_TOOL_UNSUPPORTED\n",
            b"\n",
            b"",
        ] {
            assert_eq!(tail_of(&[line]).reported_code(), None, "{line:?}");
        }
    }

    #[test]
    fn earlier_line_code_is_superseded_by_a_later_line() {
        assert_eq!(
            tail_of(&[b"PI_PROVIDER_ERROR\nsome traceback\n"]).reported_code(),
            None
        );
        assert_eq!(
            tail_of(&[b"PI_PROVIDER_ERROR\n\n"]).reported_code(),
            None,
            "a trailing blank line hides the earlier code"
        );
    }

    #[test]
    fn unterminated_final_fragment_is_not_reported() {
        assert_eq!(tail_of(&[b"PI_PROVIDER_ERROR"]).reported_code(), None);
        assert_eq!(
            tail_of(&[b"PI_PROVIDER_ERROR\nAGENT_TIMEOUT"]).reported_code(),
            Some("PI_PROVIDER_ERROR"),
            "the last COMPLETE line is the earlier one"
        );
    }

    #[test]
    fn over_long_lines_are_discarded_without_unbounded_buffering() {
        let mut tail = AgentStderrTail::default();
        tail.observe(b"PI_PROVIDER_ERROR\n");
        let long = vec![b'x'; 1_000_000];
        tail.observe(&long);
        assert!(tail.current.capacity() <= MAX_AGENT_ERROR_LINE_BYTES * 2);
        tail.observe(b"\n");
        assert_eq!(tail.reported_code(), None);
        // Exactly 64 bytes is still considered; 65 is not.
        let at_limit = format!("{}\n", "A".repeat(MAX_AGENT_ERROR_LINE_BYTES));
        assert!(tail_of(&[at_limit.as_bytes()]).last_line.is_some());
        let over = format!("{}\n", "A".repeat(MAX_AGENT_ERROR_LINE_BYTES + 1));
        assert!(tail_of(&[over.as_bytes()]).last_line.is_none());
        // The tail recovers on the next valid line.
        tail.observe(b"AGENT_CANCELLED\n");
        assert_eq!(tail.reported_code(), Some("AGENT_CANCELLED"));
    }
}
