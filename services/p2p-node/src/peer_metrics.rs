//! Bounded metrics sourced from admitted Noise connections and verified receipts.
//! Receipt latency is end-to-end admission acknowledgement, not a ping RTT.
use serde::Serialize;
use std::collections::{HashMap, VecDeque};
use std::time::Instant;

#[derive(Clone, Debug, Serialize)]
pub struct LatencySample {
    pub elapsed_ms: u64,
    pub receipt_latency_ms: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct PeerMetric {
    pub peer_id: String,
    pub connected: bool,
    pub connections: u64,
    pub disconnects: u64,
    pub published_messages: u64,
    pub cryptographically_verified_receipts: u64,
    pub unconfirmed_timeouts: u64,
    pub latency_history: VecDeque<LatencySample>,
}

pub struct PeerMetrics {
    started: Instant,
    peers: HashMap<String, PeerMetric>,
    pending: HashMap<(String, String), Instant>,
}

impl Default for PeerMetrics {
    fn default() -> Self {
        Self { started: Instant::now(), peers: HashMap::new(), pending: HashMap::new() }
    }
}

impl PeerMetrics {
    fn peer(&mut self, peer: &str) -> Option<&mut PeerMetric> {
        if peer.is_empty() || peer.len() > 128 || (!self.peers.contains_key(peer) && self.peers.len() >= 256) { return None; }
        Some(self.peers.entry(peer.to_owned()).or_insert_with(|| PeerMetric {
            peer_id: peer.to_owned(), connected: false, connections: 0, disconnects: 0,
            published_messages: 0, cryptographically_verified_receipts: 0,
            unconfirmed_timeouts: 0, latency_history: VecDeque::new(),
        }))
    }
    pub fn connected(&mut self, peer: &str) {
        if let Some(metric) = self.peer(peer) {
            metric.connected = true; metric.connections = metric.connections.saturating_add(1);
        }
    }
    pub fn disconnected(&mut self, peer: &str) {
        if let Some(metric) = self.peers.get_mut(peer) {
            metric.connected = false; metric.disconnects = metric.disconnects.saturating_add(1);
        }
    }
    pub fn sent(&mut self, peer: &str, message_id: &str, now: Instant) {
        if message_id.len() > 128 || self.pending.len() >= 2048 { return; }
        let key = (peer.to_owned(), message_id.to_owned());
        if self.pending.contains_key(&key) { return; }
        if let Some(metric) = self.peer(peer) {
            metric.published_messages = metric.published_messages.saturating_add(1);
            self.pending.insert(key, now);
        }
    }
    pub fn verified_receipt(&mut self, peer: &str, message_id: &str, now: Instant) {
        if let Some(sent) = self.pending.remove(&(peer.to_owned(), message_id.to_owned())) {
            let Some(latency) = now.checked_duration_since(sent) else { return; };
            let elapsed = now.saturating_duration_since(self.started).as_millis() as u64;
            if let Some(metric) = self.peer(peer) {
                metric.cryptographically_verified_receipts = metric.cryptographically_verified_receipts.saturating_add(1);
                if metric.latency_history.len() == 60 { metric.latency_history.pop_front(); }
                metric.latency_history.push_back(LatencySample { elapsed_ms: elapsed, receipt_latency_ms: latency.as_millis() as u64 });
            }
        }
    }
    pub fn timeout(&mut self, peer: &str, message_id: &str) {
        if self.pending.remove(&(peer.to_owned(), message_id.to_owned())).is_some() {
            if let Some(metric) = self.peer(peer) { metric.unconfirmed_timeouts = metric.unconfirmed_timeouts.saturating_add(1); }
        }
    }
    pub fn snapshot(&self) -> Vec<PeerMetric> {
        let mut result = self.peers.values().cloned().collect::<Vec<_>>();
        result.sort_by(|a, b| a.peer_id.cmp(&b.peer_id)); result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;
    #[test]
    fn receipts_require_a_pending_send_and_do_not_double_count() {
        let mut metrics = PeerMetrics::default(); let now = Instant::now();
        metrics.connected("peer-a");
        metrics.verified_receipt("peer-a", "unsolicited", now);
        metrics.sent("peer-a", "one", now);
        metrics.verified_receipt("peer-a", "one", now + Duration::from_millis(75));
        metrics.verified_receipt("peer-a", "one", now + Duration::from_millis(76));
        metrics.disconnected("peer-a");
        let peer = metrics.snapshot().pop().unwrap();
        assert_eq!(peer.cryptographically_verified_receipts, 1);
        assert_eq!(peer.latency_history[0].receipt_latency_ms, 75);
        assert_eq!(peer.disconnects, 1); assert!(!peer.connected);
    }
    #[test]
    fn histories_and_pending_maps_are_bounded() {
        let mut metrics = PeerMetrics::default(); let now = Instant::now();
        for i in 0..100 { metrics.sent("a", &i.to_string(), now); metrics.verified_receipt("a", &i.to_string(), now); }
        assert_eq!(metrics.snapshot()[0].latency_history.len(), 60);
        for i in 0..3000 { metrics.sent("a", &format!("pending-{i}"), now); }
        assert_eq!(metrics.pending.len(), 2048);
    }
}
