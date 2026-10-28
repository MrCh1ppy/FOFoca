//! Synchronous, memory-only daily sessions and exact stateless settlement.

mod arrival;
pub mod decimal;
pub mod market;
pub mod model;
#[cfg(feature = "python")]
mod python;
mod report;
mod session;
pub mod settlement;

pub use market::{MarketError, MarketSnapshot};
pub use session::{
    AdvanceOutcome, Advanced, Config, ConfigError, Session, SessionError, Snapshot, Status,
};
