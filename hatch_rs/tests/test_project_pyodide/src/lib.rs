use pyo3::prelude::*;

#[pyfunction]
fn answer() -> u8 {
    42
}

#[pymodule]
fn pyodide_project(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(answer, module)?)?;
    Ok(())
}
