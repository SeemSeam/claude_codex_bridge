//! Control queue runtime module.
//!
//! Mirrors Python lib/message_bureau/control_queue_runtime/.

pub mod ack;
pub mod common;
pub mod events;
pub mod views;

pub mod views_runtime {
    //! Views runtime submodule.
    //!
    //! Mirrors Python lib/message_bureau/control_queue_runtime/views_runtime/.

    pub mod agent;
    pub mod common;
    pub mod inbox;
    pub mod summary;
}
