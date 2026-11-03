//! Single native composition point; the engine core remains network-free.
#[cfg(feature = "python")]
use pyo3::{create_exception, exceptions::PyRuntimeError, prelude::*};

#[cfg(feature = "python")]
create_exception!(fofoca_local.data, ConfigurationError, PyRuntimeError);
#[cfg(feature = "python")]
create_exception!(fofoca_local.data, InputError, PyRuntimeError);
#[cfg(feature = "python")]
create_exception!(fofoca_local.data, TransportError, PyRuntimeError);
#[cfg(feature = "python")]
create_exception!(fofoca_local.data, HttpError, PyRuntimeError);
#[cfg(feature = "python")]
create_exception!(fofoca_local.data, ResponseError, PyRuntimeError);

#[cfg(feature = "python")]
fn exception(e: fofoca_local_data::Error) -> PyErr {
    use fofoca_local_data::Error;
    let message = e.to_string();
    match e {
        Error::Configuration => ConfigurationError::new_err(message),
        Error::Input => InputError::new_err(message),
        Error::Transport { .. } => TransportError::new_err(message),
        Error::Http { .. } => HttpError::new_err(message),
        Error::Response => ResponseError::new_err(message),
    }
}

#[cfg(feature = "python")]
#[pyclass(frozen, module = "fofoca_local.data", name = "Config")]
struct PythonConfig(fofoca_local_data::Config);

#[cfg(feature = "python")]
#[pymethods]
impl PythonConfig {
    #[new]
    fn new(
        base_url: &str,
        username: &str,
        password: &str,
        connect_timeout: f64,
        timeout: f64,
    ) -> PyResult<Self> {
        let duration = |v| {
            std::time::Duration::try_from_secs_f64(v)
                .map_err(|_| ConfigurationError::new_err("Invalid client configuration"))
        };
        fofoca_local_data::Config::new(
            base_url,
            username,
            password,
            duration(connect_timeout)?,
            duration(timeout)?,
        )
        .map(Self)
        .map_err(exception)
    }
    fn __repr__(&self) -> &'static str {
        "Config([REDACTED])"
    }
}

#[cfg(feature = "python")]
#[pyfunction]
#[pyo3(signature = (code, start, end, *, config))]
fn fetch_nav_history<'py>(
    py: Python<'py>,
    code: String,
    start: String,
    end: String,
    config: &PythonConfig,
) -> PyResult<Bound<'py, pyo3::types::PyDict>> {
    let history = py
        .detach(|| fofoca_local_data::fetch_nav_history(&code, &start, &end, &config.0))
        .map_err(exception)?;
    // The Python facade constructs immutable typed records from this lossless projection.
    let result = pyo3::types::PyDict::new(py);
    result.set_item("code", history.code)?;
    result.set_item("start", history.start)?;
    result.set_item("end", history.end)?;
    let rows = pyo3::types::PyList::empty(py);
    for row in history.rows {
        let item = pyo3::types::PyDict::new(py);
        item.set_item("nav_date", row.nav_date)?;
        item.set_item("unit_nav", row.unit_nav)?;
        item.set_item("accumulated_nav", row.accumulated_nav)?;
        rows.append(item)?;
    }
    result.set_item("rows", rows)?;
    Ok(result)
}

#[cfg(feature = "python")]
#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    fofoca_engine::python::register(module)?;
    module.add_class::<PythonConfig>()?;
    module.add_function(wrap_pyfunction!(fetch_nav_history, module)?)?;
    module.add(
        "ConfigurationError",
        module.py().get_type::<ConfigurationError>(),
    )?;
    module.add("InputError", module.py().get_type::<InputError>())?;
    module.add("TransportError", module.py().get_type::<TransportError>())?;
    module.add("HttpError", module.py().get_type::<HttpError>())?;
    module.add("ResponseError", module.py().get_type::<ResponseError>())?;
    Ok(())
}
