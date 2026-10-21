//! Private Python adapter: object/type checks and exception conversion only.
use pyo3::{
    create_exception,
    exceptions::{PyNotImplementedError, PyRuntimeError},
    prelude::*,
    types::{PyDate, PyDict, PyList},
};

use crate::{Session, SessionError};

create_exception!(fofoca_engine._native, NotInitializedError, PyRuntimeError);

fn exception(error: SessionError) -> PyErr {
    match error {
        SessionError::NotImplemented => PyNotImplementedError::new_err(error.to_string()),
        SessionError::NotInitialized => NotInitializedError::new_err(error.to_string()),
    }
}

#[pyclass(module = "fofoca_engine._native", name = "Session")]
struct PythonSession {
    session: Session,
}

#[pymethods]
impl PythonSession {
    #[new]
    fn new() -> Self {
        Self { session: Session }
    }

    // Signatures reserve date/decimal-string inputs, not business validation.
    // No configuration is retained before initialization is implemented.
    fn initialize(
        &mut self,
        start_date: &Bound<'_, PyDate>,
        end_date: &Bound<'_, PyDate>,
        initial_cash: &str,
    ) -> PyResult<()> {
        let _ = (start_date, end_date, initial_cash);
        self.session.initialize().map_err(exception)
    }

    fn account(&self) -> PyResult<()> {
        self.session.ensure_initialized().map_err(exception)
    }

    fn set_orders(&self, orders: &Bound<'_, PyList>) -> PyResult<()> {
        let _ = orders;
        self.session.ensure_initialized().map_err(exception)
    }

    fn advance_day(&self, market: &Bound<'_, PyDict>) -> PyResult<()> {
        let _ = market;
        self.session.ensure_initialized().map_err(exception)
    }

    fn report(&self) -> PyResult<()> {
        self.session.ensure_initialized().map_err(exception)
    }
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<PythonSession>()?;
    module.add(
        "NotInitializedError",
        module.py().get_type::<NotInitializedError>(),
    )?;
    Ok(())
}
