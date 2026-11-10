pub const BUILD_SCRIPT_HEADER_ABI_VERSION: u32 = 1;

#[no_mangle]
pub extern "C" fn build_script_header_answer() -> i32 {
    9
}

#[no_mangle]
pub extern "C" fn build_script_header_abi_version() -> u32 {
    BUILD_SCRIPT_HEADER_ABI_VERSION
}
