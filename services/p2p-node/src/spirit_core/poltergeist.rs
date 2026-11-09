//! Auditable jittered health heartbeat with SHA-256 integrity fingerprints.
//! Not anti-forensics, self-modifying code, or an identity rotation protocol.
use sha2::{Digest, Sha256};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

fn fingerprint(seed: &[u8], cycle: u64) -> String {
    let mut hasher = Sha256::new();
    hasher.update(seed);
    hasher.update(cycle.to_be_bytes());
    format!("{:x}", hasher.finalize())
}

fn interval_secs(cycle: u64) -> u64 {
    // Bounded deterministic jitter, useful for avoiding synchronized heartbeats.
    5 + cycle.wrapping_mul(0x9E3779B97F4A7C15).rotate_right(29) % 10
}

#[tokio::main]
async fn main() {
    println!("OSIRIS Poltergeist: auditable heartbeat / diagnostics");
    let mut cycle = 0_u64;
    let seed = std::env::var("OSIRIS_HEARTBEAT_SEED")
        .unwrap_or_else(|_| "osiris-local-diagnostic".to_string());
    loop {
        let timestamp = SystemTime::now().duration_since(UNIX_EPOCH)
            .map(|elapsed| elapsed.as_secs()).unwrap_or_default();
        println!("heartbeat cycle={cycle} timestamp={timestamp} fingerprint={}",
            &fingerprint(seed.as_bytes(), cycle)[..16]);
        let wait = Duration::from_secs(interval_secs(cycle));
        cycle = cycle.wrapping_add(1);
        tokio::select! {
            _ = tokio::signal::ctrl_c() => { println!("heartbeat stopped"); break; }
            _ = tokio::time::sleep(wait) => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fingerprint_changes_by_cycle_and_seed() {
        assert_eq!(fingerprint(b"seed", 1), fingerprint(b"seed", 1));
        assert_ne!(fingerprint(b"seed", 1), fingerprint(b"seed", 2));
        assert_ne!(fingerprint(b"seed", 1), fingerprint(b"other", 1));
    }
    #[test]
    fn heartbeat_jitter_is_bounded() {
        for cycle in 0..1000 {
            assert!((5..15).contains(&interval_secs(cycle)));
        }
    }
}
