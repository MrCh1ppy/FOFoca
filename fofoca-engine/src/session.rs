use snafu::Snafu;

/// Synchronous, memory-only, permanently uninitialized phase-1 session.
#[derive(Default)]
pub struct Session;

#[derive(Debug, Snafu)]
pub enum SessionError {
    #[snafu(display("Initialization is not implemented in the phase-1 skeleton"))]
    NotImplemented,
    #[snafu(display(
        "Session is not initialized; business commands are not implemented in phase 1"
    ))]
    NotInitialized,
}

impl Session {
    /// Reject initialization until typed configuration and account semantics exist.
    /// The argument-free core placeholder does not accept wire or Python objects.
    pub fn initialize(&mut self) -> Result<(), SessionError> {
        Err(SessionError::NotImplemented)
    }

    pub fn ensure_initialized(&self) -> Result<(), SessionError> {
        Err(SessionError::NotInitialized)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn initialization_remains_unimplemented_without_changing_state() {
        let mut session = Session;
        for _ in 0..2 {
            assert!(matches!(
                session.ensure_initialized(),
                Err(SessionError::NotInitialized)
            ));
            assert!(matches!(
                session.initialize(),
                Err(SessionError::NotImplemented)
            ));
            assert!(matches!(
                session.ensure_initialized(),
                Err(SessionError::NotInitialized)
            ));
        }
    }
}
