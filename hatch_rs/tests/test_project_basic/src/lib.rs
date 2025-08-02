use pyo3::prelude::*;

#[pyfunction]
pub fn hello() -> &'static str {
    "A string"
}

#[pymodule]
fn project(_py: Python, m: &Bound<PyModule>) -> PyResult<()> {
    // Example
    m.add_function(pyo3::wrap_pyfunction!(hello, m)?)?;
    Ok(())
}
