pub const C_ABI_LIBRARY_ABI_VERSION: u32 = 1;

#[no_mangle]
pub extern "C" fn c_abi_library_answer() -> i32 {
    7
}

#[no_mangle]
pub extern "C" fn c_abi_library_abi_version() -> u32 {
    C_ABI_LIBRARY_ABI_VERSION
}
