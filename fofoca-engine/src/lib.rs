//! Local, synchronous, memory-only backtest skeleton. No business implementation yet.

#[cfg(feature = "cli")]
mod cli;
mod decimal;
mod model;
#[cfg(feature = "cli")]
mod protocol;
#[cfg(feature = "python")]
mod python;
mod report;
mod session;
mod settlement;

#[cfg(feature = "cli")]
pub use cli::{CliError, run};
pub use session::{Session, SessionError};
