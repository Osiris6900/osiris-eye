//! Bounded diagnostic throttling only. Never changes peer admission decisions.
use std::collections::HashMap;
use std::time::{Duration, Instant};

const WINDOW: Duration = Duration::from_secs(60);
const MAX_KEYS: usize = 4096;

#[derive(Debug)]
struct Entry {
    last_emit: Instant,
    suppressed: u64,
}

#[derive(Default, Debug)]
pub(crate) struct RejectionLogThrottle {
    entries: HashMap<(String, String, String), Entry>,
}

impl RejectionLogThrottle {
    /// Emit the first rejection for each (topic, peer, reason), then aggregate
    /// repeats. Each new peer gets an immediate diagnostic.
    pub(crate) fn rejected(&mut self, topic: &str, peer: &str, reason: &str) {
        let now = Instant::now();
        let key = (topic.to_owned(), peer.to_owned(), reason.to_owned());
        if let Some(entry) = self.entries.get_mut(&key) {
            if now.duration_since(entry.last_emit) >= WINDOW {
                if entry.suppressed > 0 {
                    println!("peer_not_allowed_rejected_suppressed topic={} peer={} reason={} count={} window_secs=60", topic, peer, reason, entry.suppressed);
                }
                entry.suppressed = 0;
                entry.last_emit = now;
                println!("peer_not_allowed_rejected topic={} peer={} reason={}", topic, peer, reason);
            } else {
                entry.suppressed = entry.suppressed.saturating_add(1);
            }
            return;
        }
        // Bounded memory under peer churn: evict oldest tracked key. The next
        // rejection for an evicted key is logged immediately, never hidden.
        if self.entries.len() >= MAX_KEYS {
            if let Some(oldest) = self.entries.iter().min_by_key(|(_, v)| v.last_emit).map(|(k, _)| k.clone()) {
                if let Some(previous) = self.entries.remove(&oldest) {
                    if previous.suppressed > 0 {
                        println!("peer_not_allowed_rejected_suppressed topic={} peer={} reason={} count={} window_secs=60", oldest.0, oldest.1, oldest.2, previous.suppressed);
                    }
                }
            }
        }
        self.entries.insert(key, Entry { last_emit: now, suppressed: 0 });
        println!("peer_not_allowed_rejected topic={} peer={} reason={}", topic, peer, reason);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn repeat_is_suppressed_but_new_peer_is_immediate() {
        let mut t = RejectionLogThrottle::default();
        t.rejected("keys", "peer-a", "peer_not_allowed");
        t.rejected("keys", "peer-a", "peer_not_allowed");
        t.rejected("keys", "peer-b", "peer_not_allowed");
        assert_eq!(t.entries.len(), 2);
        assert_eq!(t.entries[&("keys".into(), "peer-a".into(), "peer_not_allowed".into())].suppressed, 1);
        assert_eq!(t.entries[&("keys".into(), "peer-b".into(), "peer_not_allowed".into())].suppressed, 0);
    }
    #[test]
    fn window_resets_counter() {
        let mut t = RejectionLogThrottle::default();
        t.rejected("keys", "peer-a", "peer_not_allowed");
        let key = ("keys".into(), "peer-a".into(), "peer_not_allowed".into());
        t.entries.get_mut(&key).unwrap().last_emit = Instant::now() - WINDOW;
        t.rejected("keys", "peer-a", "peer_not_allowed");
        assert_eq!(t.entries[&key].suppressed, 0);
    }
    #[test]
    fn bounded_under_churn() {
        let mut t = RejectionLogThrottle::default();
        for i in 0..MAX_KEYS + 1 {
            t.rejected("keys", &format!("peer-{i}"), "peer_not_allowed");
        }
        assert_eq!(t.entries.len(), MAX_KEYS);
    }
}
