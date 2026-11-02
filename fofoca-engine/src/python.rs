//! Private Python adapter: object/type checks and exception conversion only.
use pyo3::{
    create_exception,
    exceptions::{
        PyNotImplementedError, PyOverflowError, PyRuntimeError, PyTypeError, PyValueError,
    },
    prelude::*,
    types::{PyDate, PyDateAccess, PyDateTime, PyDict, PyList, PyMapping},
};

use crate::{
    AdvanceOutcome, MarketSnapshot, Status,
    decimal::{Nav, Shares},
    model::{Account, Code, Holding, Order, Side},
    settlement::{Issue, NetShares, Skip, SkipReason},
};
use crate::{Config, ConfigError, Session, SessionError, Snapshot, decimal::DecimalError};
use chrono::{Datelike, NaiveDate};
use std::collections::BTreeMap;

create_exception!(fofoca_engine._native, NotInitializedError, PyRuntimeError);
create_exception!(fofoca_engine._native, FinishedError, PyRuntimeError);
create_exception!(fofoca_engine._native, MarketConflictError, PyRuntimeError);
create_exception!(
    fofoca_engine._native,
    AlreadyInitializedError,
    PyRuntimeError
);

fn exception(error: SessionError) -> PyErr {
    match error {
        SessionError::NotImplemented => PyNotImplementedError::new_err(error.to_string()),
        SessionError::NotInitialized => NotInitializedError::new_err(error.to_string()),
        SessionError::AlreadyInitialized => AlreadyInitializedError::new_err(error.to_string()),
        SessionError::Finished => FinishedError::new_err(error.to_string()),
        SessionError::MarketConflict => MarketConflictError::new_err(error.to_string()),
        SessionError::Market { .. } => PyValueError::new_err(error.to_string()),
    }
}

fn config_exception(error: ConfigError) -> PyErr {
    match &error {
        ConfigError::InitialCash {
            source: DecimalError::Overflow,
        } => PyOverflowError::new_err(error.to_string()),
        _ => PyValueError::new_err(error.to_string()),
    }
}

fn date(value: &Bound<'_, PyDate>) -> PyResult<NaiveDate> {
    if value.is_instance_of::<PyDateTime>() {
        return Err(PyTypeError::new_err(
            "Expected datetime.date, not datetime.datetime",
        ));
    }
    NaiveDate::from_ymd_opt(
        value.get_year(),
        value.get_month().into(),
        value.get_day().into(),
    )
    .ok_or_else(|| PyValueError::new_err("Invalid calendar date"))
}

fn python_date(py: Python<'_>, value: NaiveDate) -> PyResult<Bound<'_, PyDate>> {
    PyDate::new(py, value.year(), value.month() as u8, value.day() as u8)
}

fn decimal_exception(error: DecimalError) -> PyErr {
    match error {
        DecimalError::Overflow => PyOverflowError::new_err(error.to_string()),
        _ => PyValueError::new_err(error.to_string()),
    }
}

fn code(value: &Bound<'_, PyAny>) -> PyResult<Code> {
    Code::parse(&value.extract::<String>()?).map_err(|e| PyValueError::new_err(e.to_string()))
}

fn orders(value: &Bound<'_, PyList>) -> PyResult<Vec<Order>> {
    value
        .iter()
        .map(|item| {
            let mapping = item.cast::<PyMapping>()?;
            let field = |name: &str| {
                mapping
                    .get_item(name)
                    .map_err(|_| PyTypeError::new_err(format!("Missing order field: {name}")))
            };
            let code = code(&field("code")?)?;
            let side = match field("side")?.extract::<String>()?.as_str() {
                "buy" => Side::Buy,
                "sell" => Side::Sell,
                _ => return Err(PyValueError::new_err("Order side must be buy or sell")),
            };
            let shares = Shares::parse_positive(&field("shares")?.extract::<String>()?)
                .map_err(decimal_exception)?;
            Order::new(code, side, shares).map_err(|e| PyValueError::new_err(e.to_string()))
        })
        .collect()
}

fn market(value: &Bound<'_, PyMapping>, day: NaiveDate) -> PyResult<MarketSnapshot> {
    let mut prices = BTreeMap::new();
    for item in value.items()?.try_iter()? {
        let (key, value): (Bound<'_, PyAny>, Bound<'_, PyAny>) = item?.extract()?;
        let code = code(&key)?;
        let nav = if value.is_none() {
            None
        } else {
            Some(Nav::parse(&value.extract::<String>()?).map_err(decimal_exception)?)
        };
        prices.insert(code, nav);
    }
    MarketSnapshot::new(day, prices).map_err(|e| PyValueError::new_err(e.to_string()))
}

fn side(value: Side) -> &'static str {
    match value {
        Side::Buy => "buy",
        Side::Sell => "sell",
    }
}

fn net<'py>(py: Python<'py>, code: &Code, value: NetShares) -> PyResult<Bound<'py, PyDict>> {
    let result = PyDict::new(py);
    result.set_item("code", code.to_string())?;
    result.set_item("side", value.side().map(side))?;
    result.set_item("shares", value.shares().to_string())?;
    Ok(result)
}

fn skips<'py>(py: Python<'py>, values: &[Skip]) -> PyResult<Bound<'py, PyList>> {
    let result = PyList::empty(py);
    for value in values {
        let item = net(py, &value.code, value.net)?;
        item.set_item(
            "reason",
            match value.reason {
                SkipReason::MissingNav => "missing_nav",
            },
        )?;
        result.append(item)?;
    }
    Ok(result)
}

fn holding<'py>(py: Python<'py>, value: &Holding) -> PyResult<Bound<'py, PyDict>> {
    let result = PyDict::new(py);
    result.set_item("code", value.code.to_string())?;
    result.set_item("shares", value.shares.to_string())?;
    result.set_item("unit_nav", value.unit_nav.to_string())?;
    result.set_item("price_date", python_date(py, value.price_date)?)?;
    result.set_item("stale", value.stale)?;
    result.set_item("value", value.value.to_string())?;
    Ok(result)
}

fn account(py: Python<'_>, value: Account) -> PyResult<Bound<'_, PyDict>> {
    let account = PyDict::new(py);
    account.set_item("valuation_date", python_date(py, value.valuation_date)?)?;
    account.set_item("cash", value.cash.to_string())?;
    let holdings = PyList::empty(py);
    for value in value.holdings {
        holdings.append(holding(py, &value)?)?;
    }
    account.set_item("holdings", holdings)?;
    let buys = PyList::empty(py);
    for value in value.pending_buys {
        let item = holding(
            py,
            &Holding {
                code: value.code,
                shares: value.shares,
                unit_nav: value.unit_nav,
                price_date: value.price_date,
                stale: value.stale,
                value: value.value,
            },
        )?;
        item.set_item("execution_date", python_date(py, value.execution_date)?)?;
        item.set_item("due_date", python_date(py, value.due_date)?)?;
        buys.append(item)?;
    }
    account.set_item("pending_buys", buys)?;
    let cash = PyList::empty(py);
    for value in value.pending_cash {
        let item = PyDict::new(py);
        item.set_item("code", value.code.to_string())?;
        item.set_item("amount", value.amount.to_string())?;
        item.set_item("execution_date", python_date(py, value.execution_date)?)?;
        item.set_item("due_date", python_date(py, value.due_date)?)?;
        cash.append(item)?;
    }
    account.set_item("pending_cash", cash)?;
    account.set_item("confirmed_value", value.confirmed_value.to_string())?;
    account.set_item("pending_buy_value", value.pending_buy_value.to_string())?;
    account.set_item("pending_cash_value", value.pending_cash_value.to_string())?;
    account.set_item("total_assets", value.total_assets.to_string())?;
    Ok(account)
}

fn outcome(py: Python<'_>, value: AdvanceOutcome) -> PyResult<Bound<'_, PyDict>> {
    let result = PyDict::new(py);
    match value {
        AdvanceOutcome::Advanced(value) => {
            result.set_item("status", "advanced")?;
            result.set_item("closed_date", python_date(py, value.closed_date)?)?;
            result.set_item("finished", value.finished)?;
            result.set_item("account", account(py, value.account)?)?;
            result.set_item("skips", skips(py, &value.skips)?)?;
            let executions = PyList::empty(py);
            for value in value.executions {
                let item = PyDict::new(py);
                item.set_item("code", value.code.to_string())?;
                item.set_item("side", side(value.side))?;
                item.set_item("shares", value.shares.to_string())?;
                item.set_item("unit_nav", value.unit_nav.to_string())?;
                item.set_item("amount", value.amount.to_string())?;
                item.set_item("execution_date", python_date(py, value.execution_date)?)?;
                item.set_item("due_date", python_date(py, value.due_date)?)?;
                executions.append(item)?;
            }
            result.set_item("executions", executions)?;
        }
        AdvanceOutcome::Rejected(value) => {
            result.set_item("status", "rejected")?;
            let orders = PyList::empty(py);
            for value in value.orders {
                let item = PyDict::new(py);
                item.set_item("code", value.code.to_string())?;
                item.set_item("side", side(value.side))?;
                item.set_item("shares", value.shares.to_string())?;
                orders.append(item)?;
            }
            result.set_item("orders", orders)?;
            let nets = PyList::empty(py);
            for value in value.nets {
                nets.append(net(py, &value.code, value.net)?)?;
            }
            result.set_item("nets", nets)?;
            result.set_item("skips", skips(py, &value.skips)?)?;
            let issues = PyList::empty(py);
            for value in value.issues {
                let item = PyDict::new(py);
                let kind = match value {
                    Issue::MissingClosingNav { code } => {
                        item.set_item("code", code.to_string())?;
                        "missing_closing_nav"
                    }
                    Issue::Oversell {
                        code,
                        requested,
                        confirmed,
                    } => {
                        item.set_item("code", code.to_string())?;
                        item.set_item("requested", requested.to_string())?;
                        item.set_item("confirmed", confirmed.to_string())?;
                        "oversell"
                    }
                    Issue::InsufficientCash {
                        required,
                        available,
                    } => {
                        item.set_item("required", required.to_string())?;
                        item.set_item("available", available.to_string())?;
                        "insufficient_cash"
                    }
                    Issue::ArithmeticOverflow { code } => {
                        item.set_item("code", code.map(|c| c.to_string()))?;
                        "arithmetic_overflow"
                    }
                    Issue::DueDateOverflow => "due_date_overflow",
                };
                item.set_item("kind", kind)?;
                issues.append(item)?;
            }
            result.set_item("issues", issues)?;
        }
    }
    Ok(result)
}

fn snapshot(py: Python<'_>, value: Snapshot) -> PyResult<Bound<'_, PyDict>> {
    let result = PyDict::new(py);
    result.set_item(
        "status",
        match value.status {
            Status::Running => "running",
            Status::Finished => "finished",
        },
    )?;
    result.set_item("start_date", python_date(py, value.start_date)?)?;
    result.set_item("end_date", python_date(py, value.end_date)?)?;
    result.set_item("current_date", python_date(py, value.current_date)?)?;
    result.set_item(
        "last_closed_date",
        value
            .last_closed_date
            .map(|d| python_date(py, d))
            .transpose()?,
    )?;
    result.set_item("snapshot_pinned", value.snapshot_pinned)?;
    result.set_item("orders", PyList::empty(py))?;
    result.set_item("account", account(py, value.account)?)?;
    Ok(result)
}

#[pyclass(module = "fofoca_engine._native", name = "Session")]
struct PythonSession {
    session: Session,
}

#[pymethods]
impl PythonSession {
    #[new]
    fn new() -> Self {
        Self {
            session: Session::default(),
        }
    }

    fn initialize(
        &mut self,
        start_date: &Bound<'_, PyDate>,
        end_date: &Bound<'_, PyDate>,
        initial_cash: &str,
    ) -> PyResult<()> {
        let (start_date, end_date) = (date(start_date)?, date(end_date)?);
        if self.session.ensure_initialized().is_ok() {
            return Err(exception(SessionError::AlreadyInitialized));
        }
        let config = Config::parse(start_date, end_date, initial_cash).map_err(config_exception)?;
        self.session.initialize(config).map_err(exception)
    }

    fn account<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        snapshot(py, self.session.account().map_err(exception)?)
    }

    fn advance_day<'py>(
        &mut self,
        py: Python<'py>,
        orders: &Bound<'_, PyList>,
        market: &Bound<'_, PyMapping>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let day = self.session.account().map_err(exception)?.current_date;
        let orders = crate::python::orders(orders)?;
        let market = crate::python::market(market, day)?;
        outcome(
            py,
            self.session
                .advance_day(&orders, &market)
                .map_err(exception)?,
        )
    }

    fn report(&self) -> PyResult<()> {
        self.session.ensure_initialized().map_err(exception)?;
        Err(exception(SessionError::NotImplemented))
    }
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<PythonSession>()?;
    module.add("FinishedError", module.py().get_type::<FinishedError>())?;
    module.add(
        "MarketConflictError",
        module.py().get_type::<MarketConflictError>(),
    )?;
    module.add(
        "AlreadyInitializedError",
        module.py().get_type::<AlreadyInitializedError>(),
    )?;
    module.add(
        "NotInitializedError",
        module.py().get_type::<NotInitializedError>(),
    )?;
    Ok(())
}
