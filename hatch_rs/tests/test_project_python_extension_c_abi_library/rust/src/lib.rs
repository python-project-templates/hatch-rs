#[no_mangle]
pub extern "C" fn c_abi_library_answer() -> i32 {
    7
}

#[no_mangle]
pub extern "C" fn c_abi_library_abi_version() -> u32 {
    1
}
