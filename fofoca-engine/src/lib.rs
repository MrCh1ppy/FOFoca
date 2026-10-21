//! Local, synchronous, memory-only backtest skeleton. No business implementation yet.

mod decimal;
mod model;
#[cfg(feature = "python")]
mod python;
mod report;
mod session;
mod settlement;

pub use session::{Session, SessionError};
