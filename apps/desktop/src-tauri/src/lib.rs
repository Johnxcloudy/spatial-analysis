#[cfg_attr(test, allow(dead_code))]
mod protocol;

#[cfg(not(test))]
mod app;
#[cfg_attr(test, allow(dead_code))]
mod engine;
#[cfg_attr(test, allow(dead_code))]
mod job;

#[cfg(not(test))]
pub use app::run;
