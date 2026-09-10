#[cfg_attr(test, allow(dead_code))]
mod protocol;

#[cfg(not(test))]
mod app;
#[cfg_attr(test, allow(dead_code))]
mod engine;
#[cfg_attr(test, allow(dead_code))]
mod job;
#[cfg_attr(test, allow(dead_code))]
mod rpc_timing;

#[cfg(not(test))]
pub use app::run;
