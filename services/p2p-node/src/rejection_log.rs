//! Bounded diagnostic throttling only. Never changes peer admission.
//!
//! Issue #255 sketched both a per-peer first line and a map keyed only by
//! `(topic, reason)`. A shared counter would hide the first rejection from a
//! newly observed peer until that shared window elapsed. State is therefore
//! keyed by `(topic, peer, reason)`: the first event for each triple is
//! logged immediately, and only repeats of that same triple are counted.
//!
//! Aggregate lines use the prefix `peer_not_allowed_rejected_suppressed`,
//! which `scripts/run_p2p_soak.py` already sums by reason. The count is the
//! number of repeats that were not themselves printed. The event that opens
//! the next window is printed again and is not included in that count.
//!
//! Aggregates are emitted when the next repeat arrives at or after the
//! window, when [`RejectionLogThrottle::flush_elapsed`] runs after a quiet
//! window, or when an entry is evicted. A hard kill can still drop the open
//! window. The node flushes on the 60s tick and on SIGINT/SIGTERM.

use std::collections::HashMap;
use std::time::{Duration, Instant};

pub(crate) const WINDOW: Duration = Duration::from_secs(60);
pub(crate) const MAX_KEYS: usize = 4096;

#[derive(Debug)]
struct Entry {
    last_emit: Instant,
    suppressed: u64,
}

#[derive(Debug, Default)]
pub(crate) struct RejectionLogThrottle {
    entries: HashMap<(String, String, String), Entry>,
}

impl RejectionLogThrottle {
    /// Record one fail-closed rejection. Returned lines are the only output;
    /// this method does not admit the peer or the message.
    pub(crate) fn rejected(
        &mut self,
        topic: &str,
        peer: &str,
        reason: &str,
        now: Instant,
    ) -> Vec<String> {
        let key = (topic.to_owned(), peer.to_owned(), reason.to_owned());
        if let Some(entry) = self.entries.get_mut(&key) {
            if now.saturating_duration_since(entry.last_emit) >= WINDOW {
                let mut lines = Vec::new();
                if entry.suppressed > 0 {
                    lines.push(suppressed_line(topic, peer, reason, entry.suppressed));
                }
                entry.suppressed = 0;
                entry.last_emit = now;
                lines.push(visible_line(topic, peer, reason));
                return lines;
            }
            entry.suppressed = entry.suppressed.saturating_add(1);
            return Vec::new();
        }

        let mut lines = Vec::new();
        // Bound memory under peer churn. Evict the oldest tracked key and
        // flush its open count first. The next rejection for an evicted key
        // is logged immediately, never held back by a peer that remains.
        if self.entries.len() >= MAX_KEYS {
            if let Some(oldest) = self.oldest_key() {
                if let Some(previous) = self.entries.remove(&oldest) {
                    if previous.suppressed > 0 {
                        lines.push(suppressed_line(
                            &oldest.0,
                            &oldest.1,
                            &oldest.2,
                            previous.suppressed,
                        ));
                    }
                }
            }
        }
        self.entries.insert(
            key,
            Entry {
                last_emit: now,
                suppressed: 0,
            },
        );
        lines.push(visible_line(topic, peer, reason));
        lines
    }

    /// Emit aggregates whose window has elapsed with no further event.
    /// Entries stay tracked, so the next repeat is still a repeat.
    pub(crate) fn flush_elapsed(&mut self, now: Instant) -> Vec<String> {
        let mut due: Vec<(String, String, String)> = self
            .entries
            .iter()
            .filter(|(_, entry)| {
                entry.suppressed > 0 && now.saturating_duration_since(entry.last_emit) >= WINDOW
            })
            .map(|(key, _)| key.clone())
            .collect();
        due.sort();
        let mut lines = Vec::with_capacity(due.len());
        for key in due {
            if let Some(entry) = self.entries.get_mut(&key) {
                if entry.suppressed > 0 {
                    lines.push(suppressed_line(&key.0, &key.1, &key.2, entry.suppressed));
                    entry.suppressed = 0;
                    entry.last_emit = now;
                }
            }
        }
        lines
    }

    fn oldest_key(&self) -> Option<(String, String, String)> {
        self.entries
            .iter()
            .min_by(|left, right| {
                left.1
                    .last_emit
                    .cmp(&right.1.last_emit)
                    .then_with(|| left.0.cmp(right.0))
            })
            .map(|(key, _)| key.clone())
    }
}

fn log_token(value: &str) -> String {
    let token: String = value
        .chars()
        .filter(|ch| !ch.is_control() && !ch.is_whitespace() && *ch != '=')
        .take(256)
        .collect();
    if token.is_empty() {
        "invalid".to_owned()
    } else {
        token
    }
}

fn visible_line(topic: &str, peer: &str, reason: &str) -> String {
    format!(
        "peer_not_allowed_rejected topic={} peer={} reason={}",
        log_token(topic),
        log_token(peer),
        log_token(reason),
    )
}

fn suppressed_line(topic: &str, peer: &str, reason: &str, count: u64) -> String {
    format!(
        "peer_not_allowed_rejected_suppressed topic={} peer={} reason={} count={} window_secs={}",
        log_token(topic),
        log_token(peer),
        log_token(reason),
        count,
        WINDOW.as_secs(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(topic: &str, peer: &str, reason: &str) -> (String, String, String) {
        (topic.to_owned(), peer.to_owned(), reason.to_owned())
    }

    fn assert_parser_safe(line: &str) {
        assert!(!line.contains('\n') && !line.contains('\r'), "{line}");
        let mut fields = line.split_whitespace();
        let prefix = fields.next().unwrap_or("");
        assert!(
            prefix == "peer_not_allowed_rejected"
                || prefix == "peer_not_allowed_rejected_suppressed",
            "{line}"
        );
        assert!(
            fields.all(|field| field.matches('=').count() == 1),
            "{line}"
        );
        if let Some(rest) = line.strip_prefix("peer_not_allowed_rejected_suppressed ") {
            let count = rest
                .split_whitespace()
                .find_map(|field| field.strip_prefix("count="))
                .and_then(|value| value.parse::<u64>().ok());
            assert!(matches!(count, Some(value) if value >= 1), "{line}");
            assert!(rest.contains("window_secs=60"), "{line}");
        } else {
            assert!(line.starts_with("peer_not_allowed_rejected "), "{line}");
            assert!(!line.contains("count="), "{line}");
        }
    }

    #[test]
    fn first_event_is_visible_and_repeat_inside_window_is_not() {
        let start = Instant::now();
        let mut throttle = RejectionLogThrottle::default();
        let first = throttle.rejected(
            "osiris/e2ee/messages/1",
            "peer-a",
            "peer_not_allowed",
            start,
        );
        assert_eq!(
            first,
            vec!["peer_not_allowed_rejected topic=osiris/e2ee/messages/1 peer=peer-a reason=peer_not_allowed".to_owned()]
        );
        let early = start + WINDOW - Duration::from_nanos(1);
        assert!(throttle
            .rejected(
                "osiris/e2ee/messages/1",
                "peer-a",
                "peer_not_allowed",
                early
            )
            .is_empty());
        assert_eq!(
            throttle.entries[&key("osiris/e2ee/messages/1", "peer-a", "peer_not_allowed")]
                .suppressed,
            1
        );
        assert!(throttle.flush_elapsed(early).is_empty());
        assert_eq!(
            throttle.entries[&key("osiris/e2ee/messages/1", "peer-a", "peer_not_allowed")]
                .suppressed,
            1
        );
    }

    #[test]
    fn window_boundary_emits_prior_count_then_the_new_event() {
        let start = Instant::now();
        let mut throttle = RejectionLogThrottle::default();
        throttle.rejected("keys", "peer-a", "peer_not_allowed", start);
        assert!(throttle
            .rejected(
                "keys",
                "peer-a",
                "peer_not_allowed",
                start + Duration::from_secs(1)
            )
            .is_empty());
        assert!(throttle
            .rejected(
                "keys",
                "peer-a",
                "peer_not_allowed",
                start + Duration::from_secs(2)
            )
            .is_empty());
        assert!(throttle
            .rejected(
                "keys",
                "peer-a",
                "peer_not_allowed",
                start + WINDOW - Duration::from_nanos(1)
            )
            .is_empty());
        let rolled = throttle.rejected("keys", "peer-a", "peer_not_allowed", start + WINDOW);
        assert_eq!(
            rolled,
            vec![
                "peer_not_allowed_rejected_suppressed topic=keys peer=peer-a reason=peer_not_allowed count=3 window_secs=60".to_owned(),
                "peer_not_allowed_rejected topic=keys peer=peer-a reason=peer_not_allowed".to_owned(),
            ]
        );
        assert_eq!(
            throttle.entries[&key("keys", "peer-a", "peer_not_allowed")].suppressed,
            0
        );
        for line in &rolled {
            assert_parser_safe(line);
        }
    }

    #[test]
    fn quiet_flush_reports_once_and_keeps_later_repeats_suppressed() {
        let start = Instant::now();
        let mut throttle = RejectionLogThrottle::default();
        throttle.rejected("keys", "peer-a", "peer_not_allowed", start);
        throttle.rejected(
            "keys",
            "peer-a",
            "peer_not_allowed",
            start + Duration::from_secs(5),
        );
        throttle.rejected(
            "keys",
            "peer-a",
            "peer_not_allowed",
            start + Duration::from_secs(6),
        );
        assert!(throttle
            .flush_elapsed(start + WINDOW - Duration::from_nanos(1))
            .is_empty());
        let flushed = throttle.flush_elapsed(start + WINDOW);
        assert_eq!(
            flushed,
            vec!["peer_not_allowed_rejected_suppressed topic=keys peer=peer-a reason=peer_not_allowed count=2 window_secs=60".to_owned()]
        );
        assert!(throttle.flush_elapsed(start + WINDOW).is_empty());
        assert!(throttle
            .rejected(
                "keys",
                "peer-a",
                "peer_not_allowed",
                start + WINDOW + Duration::from_secs(1)
            )
            .is_empty());
        assert_eq!(
            throttle.entries[&key("keys", "peer-a", "peer_not_allowed")].suppressed,
            1
        );
        assert_eq!(throttle.entries.len(), 1);
    }

    #[test]
    fn peers_topics_and_reasons_do_not_silence_each_other() {
        let start = Instant::now();
        let mut throttle = RejectionLogThrottle::default();
        assert_eq!(
            throttle
                .rejected("keys", "peer-a", "peer_not_allowed", start)
                .len(),
            1
        );
        assert!(throttle
            .rejected("keys", "peer-a", "peer_not_allowed", start)
            .is_empty());
        assert_eq!(
            throttle
                .rejected("observations", "peer-a", "peer_not_allowed", start)
                .len(),
            1
        );
        assert_eq!(
            throttle
                .rejected("keys", "peer-a", "invalid_signature", start)
                .len(),
            1
        );
        assert_eq!(
            throttle
                .rejected("keys", "peer-b", "peer_not_allowed", start)
                .len(),
            1
        );
        assert_eq!(throttle.entries.len(), 4);
        assert_eq!(
            throttle.entries[&key("keys", "peer-a", "peer_not_allowed")].suppressed,
            1
        );
        assert_eq!(
            throttle.entries[&key("observations", "peer-a", "peer_not_allowed")].suppressed,
            0
        );
        assert_eq!(
            throttle.entries[&key("keys", "peer-a", "invalid_signature")].suppressed,
            0
        );
        assert_eq!(
            throttle.entries[&key("keys", "peer-b", "peer_not_allowed")].suppressed,
            0
        );
        let flushed = throttle.flush_elapsed(start + WINDOW);
        assert_eq!(flushed.len(), 1);
        assert!(flushed[0].contains("peer=peer-a"));
        assert!(flushed[0].contains("topic=keys"));
        assert!(flushed[0].contains("reason=peer_not_allowed"));
        assert!(flushed[0].contains("count=1"));
    }

    #[test]
    fn suppressed_counter_saturates_without_overflow() {
        let start = Instant::now();
        let mut throttle = RejectionLogThrottle::default();
        throttle.rejected("keys", "peer-a", "peer_not_allowed", start);
        let map_key = key("keys", "peer-a", "peer_not_allowed");
        throttle.entries.get_mut(&map_key).unwrap().suppressed = u64::MAX;
        assert!(throttle
            .rejected("keys", "peer-a", "peer_not_allowed", start)
            .is_empty());
        assert_eq!(throttle.entries[&map_key].suppressed, u64::MAX);
        let flushed = throttle.flush_elapsed(start + WINDOW);
        assert_eq!(flushed.len(), 1);
        assert!(flushed[0].contains(&format!("count={}", u64::MAX)));
        assert_parser_safe(&flushed[0]);
    }

    #[test]
    fn eviction_flushes_the_oldest_aggregate_and_stays_bounded() {
        let start = Instant::now();
        let mut throttle = RejectionLogThrottle::default();
        for index in 0..MAX_KEYS {
            let lines = throttle.rejected(
                "keys",
                &format!("peer-{index}"),
                "peer_not_allowed",
                start + Duration::from_millis(index as u64),
            );
            assert_eq!(
                lines.len(),
                1,
                "first sighting of peer-{index} must stay visible"
            );
            assert_parser_safe(&lines[0]);
        }
        assert_eq!(throttle.entries.len(), MAX_KEYS);
        assert!(throttle
            .rejected(
                "keys",
                "peer-0",
                "peer_not_allowed",
                start + Duration::from_millis(10)
            )
            .is_empty());
        assert!(throttle
            .rejected(
                "keys",
                "peer-0",
                "peer_not_allowed",
                start + Duration::from_millis(11)
            )
            .is_empty());
        let admitted = throttle.rejected(
            "keys",
            "peer-new",
            "peer_not_allowed",
            start + Duration::from_millis(MAX_KEYS as u64),
        );
        assert_eq!(throttle.entries.len(), MAX_KEYS);
        assert!(!throttle
            .entries
            .contains_key(&key("keys", "peer-0", "peer_not_allowed")));
        assert!(throttle
            .entries
            .contains_key(&key("keys", "peer-new", "peer_not_allowed")));
        assert_eq!(
            admitted,
            vec![
                "peer_not_allowed_rejected_suppressed topic=keys peer=peer-0 reason=peer_not_allowed count=2 window_secs=60".to_owned(),
                "peer_not_allowed_rejected topic=keys peer=peer-new reason=peer_not_allowed".to_owned(),
            ]
        );
        let again = throttle.rejected(
            "keys",
            "peer-0",
            "peer_not_allowed",
            start + Duration::from_millis(MAX_KEYS as u64),
        );
        assert_eq!(again.len(), 1);
        assert!(again[0].contains("peer=peer-0"));
        assert!(!again[0].contains("count="));
        assert_eq!(throttle.entries.len(), MAX_KEYS);
    }

    #[test]
    fn eviction_of_an_unsuppressed_key_does_not_emit_a_zero_count() {
        let start = Instant::now();
        let mut throttle = RejectionLogThrottle::default();
        for index in 0..MAX_KEYS {
            throttle.rejected(
                "keys",
                &format!("peer-{index}"),
                "peer_not_allowed",
                start + Duration::from_millis(index as u64),
            );
        }
        let lines = throttle.rejected(
            "keys",
            "peer-extra",
            "peer_not_allowed",
            start + Duration::from_secs(1),
        );
        assert_eq!(lines.len(), 1);
        assert!(!lines[0].contains("count="));
        assert_parser_safe(&lines[0]);
    }

    #[test]
    fn control_characters_cannot_split_or_forge_a_log_line() {
        let start = Instant::now();
        let mut throttle = RejectionLogThrottle::default();
        let lines = throttle.rejected(
            "top\nic",
            "peer\ninjected count=0",
            "peer_not_allowed",
            start,
        );
        assert_eq!(lines.len(), 1);
        assert_parser_safe(&lines[0]);
        assert!(!lines[0].contains("count="));
        assert_eq!(
            throttle
                .rejected("topic", "peera", "peer_not_allowed", start)
                .len(),
            1
        );
        assert_eq!(throttle.entries.len(), 2);
    }
}
