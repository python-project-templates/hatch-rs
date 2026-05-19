#[no_mangle]
pub extern "C" fn cargo_controls_answer() -> i32 {
    1
}

#[no_mangle]
pub extern "C" fn cargo_controls_env_enabled() -> i32 {
    if option_env!("HATCH_RS_CARGO_CONTROLS_TEST") == Some("enabled") {
        1
    } else {
        0
    }
}

#[cfg(feature = "ffi")]
#[no_mangle]
pub extern "C" fn cargo_controls_feature_enabled() -> i32 {
    1
}
