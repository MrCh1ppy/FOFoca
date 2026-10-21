//! Private Python adapter: object/type checks and exception conversion only.
use pyo3::{
    create_exception,
    exceptions::{
        PyNotImplementedError, PyOverflowError, PyRuntimeError, PyTypeError, PyValueError,
    },
    prelude::*,
    types::{PyDate, PyDateAccess, PyDateTime, PyDict, PyList},
};

use crate::{Config, ConfigError, Session, SessionError, Snapshot, decimal::DecimalError};
use chrono::{Datelike, NaiveDate};

create_exception!(fofoca_engine._native, NotInitializedError, PyRuntimeError);
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

fn snapshot(py: Python<'_>, value: Snapshot) -> PyResult<Bound<'_, PyDict>> {
    let result = PyDict::new(py);
    result.set_item("status", "running")?;
    result.set_item("start_date", python_date(py, value.start_date)?)?;
    result.set_item("end_date", python_date(py, value.end_date)?)?;
    result.set_item("current_date", python_date(py, value.current_date)?)?;
    result.set_item("last_closed_date", py.None())?;
    result.set_item("snapshot_pinned", value.snapshot_pinned)?;
    result.set_item("orders", PyList::empty(py))?;
    let account = PyDict::new(py);
    account.set_item(
        "valuation_date",
        python_date(py, value.account.valuation_date)?,
    )?;
    account.set_item("cash", value.account.cash.to_string())?;
    // No mutation/trading API exists in this stage: these components are empty.
    for key in ["holdings", "pending_buys", "pending_cash"] {
        account.set_item(key, PyList::empty(py))?;
    }
    account.set_item("confirmed_value", value.account.confirmed_value.to_string())?;
    account.set_item(
        "pending_buy_value",
        value.account.pending_buy_value.to_string(),
    )?;
    account.set_item(
        "pending_cash_value",
        value.account.pending_cash_value.to_string(),
    )?;
    account.set_item("total_assets", value.account.total_assets.to_string())?;
    result.set_item("account", account)?;
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

    fn set_orders(&self, orders: &Bound<'_, PyList>) -> PyResult<()> {
        let _ = orders;
        self.session.ensure_initialized().map_err(exception)?;
        Err(exception(SessionError::NotImplemented))
    }

    fn advance_day(&self, market: &Bound<'_, PyDict>) -> PyResult<()> {
        let _ = market;
        self.session.ensure_initialized().map_err(exception)?;
        Err(exception(SessionError::NotImplemented))
    }

    fn report(&self) -> PyResult<()> {
        self.session.ensure_initialized().map_err(exception)?;
        Err(exception(SessionError::NotImplemented))
    }
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<PythonSession>()?;
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
