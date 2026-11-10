#[cfg(feature = "capi")]
extern "C" {
    fn c_abi_library_answer() -> i32;
}

#[cfg(feature = "direct")]
use c_abi_library::c_abi_library_answer;

#[no_mangle]
pub extern "C" fn c_abi_bundle_extension_answer() -> i32 {
    42
}

#[no_mangle]
pub extern "C" fn c_abi_bundle_extension_library_answer() -> i32 {
    unsafe { c_abi_library_answer() }
}
