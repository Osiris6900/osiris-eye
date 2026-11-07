//! Receipt verifier and local ledger bridge shared by the node and tests.
//!
//! The swarm stays in the node binary. This library exposes the real receipt
//! checks and the authenticated spool so a test can drive them without mocking
//! verification.

pub mod delivery_receipt;

#[cfg(target_os = "linux")]
pub mod verified_receipt_bridge;

#[cfg(not(target_os = "linux"))]
pub mod verified_receipt_bridge {
    //! The peer-credential bridge is Linux-only. Configuring it elsewhere fails closed.
    use crate::delivery_receipt::VerifiedReceiptEvent;
    use std::time::Duration;

    pub struct VerifiedReceiptBridge;

    impl VerifiedReceiptBridge {
        pub fn open_configured(
            _receipts_enabled: bool,
            socket: Option<&str>,
            spool: Option<&str>,
            listener_pid: Option<&str>,
        ) -> Result<Option<Self>, String> {
            if socket.is_none() && spool.is_none() && listener_pid.is_none() {
                Ok(None)
            } else {
                Err("linux_peer_credentials_required".into())
            }
        }

        pub fn enqueue(&mut self, _event: &VerifiedReceiptEvent) -> Result<(), String> {
            Err("linux_peer_credentials_required".into())
        }

        pub fn transmit(
            &mut self,
            _event: &VerifiedReceiptEvent,
            _timeout: Duration,
        ) -> Result<String, String> {
            Err("linux_peer_credentials_required".into())
        }

        pub fn pending_events(&self, _limit: usize) -> Result<Vec<VerifiedReceiptEvent>, String> {
            Err("linux_peer_credentials_required".into())
        }
    }
}
