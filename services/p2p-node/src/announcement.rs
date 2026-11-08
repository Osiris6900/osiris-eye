//! Coalesce subscription bursts without allowing peers to postpone publication.
use std::time::{Duration, Instant};

pub struct AnnouncementGate {
    pending: bool,
    due: Instant,
}

impl AnnouncementGate {
    pub fn new(now: Instant) -> Self { Self { pending: false, due: now } }

    pub fn request(&mut self, now: Instant) {
        if !self.pending {
            self.pending = true;
            self.due = self.due.max(now + Duration::from_millis(250));
        }
    }

    pub fn ready(&self, now: Instant) -> bool { self.pending && now >= self.due }

    pub fn completed(&mut self, success: bool, now: Instant) {
        self.pending = !success;
        self.due = now + Duration::from_secs(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn burst_is_coalesced_and_repeated_requests_cannot_starve_the_timer() {
        let now = Instant::now();
        let mut gate = AnnouncementGate::new(now);
        for ms in 0..250 {
            gate.request(now + Duration::from_millis(ms));
        }
        assert!(!gate.ready(now + Duration::from_millis(249)));
        assert!(gate.ready(now + Duration::from_millis(250)));
        gate.completed(true, now + Duration::from_millis(250));
        assert!(!gate.ready(now + Duration::from_secs(20)));
    }

    #[test]
    fn failure_retries_and_new_subscribers_observe_the_global_rate_bound() {
        let now = Instant::now();
        let mut gate = AnnouncementGate::new(now);
        gate.request(now);
        let first = now + Duration::from_millis(250);
        gate.completed(false, first);
        assert!(!gate.ready(first + Duration::from_millis(999)));
        assert!(gate.ready(first + Duration::from_secs(1)));
        let success = first + Duration::from_secs(1);
        gate.completed(true, success);
        gate.request(success);
        assert!(!gate.ready(success + Duration::from_millis(999)));
        assert!(gate.ready(success + Duration::from_secs(1)));
    }
}
