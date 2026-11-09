//! OSIRIS Seraphim: bounded session guardian with secret cleanup.
//! No log deletion, binary modification, debugger evasion or forensic decoys.
use std::{env, time::{Duration, Instant}};
use tokio::time;
use zeroize::Zeroizing;

const DEFAULT_LIFETIME_SECONDS: u64 = 3600;
const MAX_LIFETIME_SECONDS: u64 = 86400;
const HEARTBEAT_SECONDS: u64 = 5;

fn lifetime_seconds(raw: Option<&str>) -> Result<u64, String> {
    match raw {
        None => Ok(DEFAULT_LIFETIME_SECONDS),
        Some(s) => {
            let seconds = s.parse::<u64>().map_err(|_| "invalid lifetime".to_string())?;
            if !(1..=MAX_LIFETIME_SECONDS).contains(&seconds) { return Err("lifetime must be 1..86400 seconds".into()); }
            Ok(seconds)
        }
    }
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let lifetime = lifetime_seconds(env::var("OSIRIS_SESSION_LIFETIME_SECONDS").ok().as_deref())?;
    // Optional session secret is never printed. Do not hardcode master keys.
    let secret = env::var("OSIRIS_SESSION_SECRET").ok().map(Zeroizing::new);
    println!("SERAPHIM START session_lifetime_seconds={lifetime} secret_configured={}", secret.is_some());
    let start = Instant::now();
    let mut ticker = time::interval(Duration::from_secs(HEARTBEAT_SECONDS));
    let stop_reason = loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => break "signal",
            _ = time::sleep_until(time::Instant::from_std(start + Duration::from_secs(lifetime))) => break "expired",
            _ = ticker.tick() => {
                println!("SERAPHIM HEARTBEAT elapsed_seconds={}", start.elapsed().as_secs());
            }
        }
    };
    println!("SERAPHIM SHUTDOWN reason={stop_reason} (audit log preserved)");
    drop(secret); // Zeroizing<String> clears the owned secret buffer on drop.
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn default_and_bounds() {
        assert_eq!(lifetime_seconds(None).unwrap(), 3600);
        assert_eq!(lifetime_seconds(Some("5")).unwrap(), 5);
        assert!(lifetime_seconds(Some("0")).is_err());
        assert!(lifetime_seconds(Some("86401")).is_err());
        assert!(lifetime_seconds(Some("not-a-number")).is_err());
    }
}
