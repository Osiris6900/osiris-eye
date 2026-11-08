use std::{collections::HashSet, error::Error, ffi::OsString};
use libp2p::PeerId;

const MAX_CONFIGURED_PEERS: usize = 16_384;
const MAX_PEER_ID_BYTES: usize = 128;
const MAX_PEER_CONFIG_BYTES: usize = MAX_CONFIGURED_PEERS * (MAX_PEER_ID_BYTES + 1);

fn validate_peer_setting(value: Option<OsString>, setting: &str) -> Result<Option<String>, Box<dyn Error>> {
    value.map(|value| value.into_string()
        .map_err(|_| format!("{setting} must contain valid UTF-8 peer IDs").into()))
        .transpose()
}

/// Invalid configured bytes must fail closed, never become an absent allowlist.
/// Error messages intentionally omit the configuration value.
pub fn read_peer_setting(setting: &str) -> Result<Option<String>, Box<dyn Error>> {
    validate_peer_setting(std::env::var_os(setting), setting)
}

fn parse_peer_set(raw: Option<&str>, setting: &str) -> Result<Option<HashSet<PeerId>>, Box<dyn Error>> {
    let Some(raw) = raw else { return Ok(None); };
    if raw.len() > MAX_PEER_CONFIG_BYTES {
        return Err(format!("{setting} exceeds maximum peer configuration size").into());
    }
    let mut peers = HashSet::new();
    for entry in raw.split(',').map(str::trim) {
        // Reject malformed separators instead of silently dropping empty entries.
        // A partially malformed operator allowlist must never be accepted.
        if entry.is_empty() {
            return Err(format!("{setting} contains an empty peer ID").into());
        }
        if entry.len() > MAX_PEER_ID_BYTES {
            return Err(format!("{setting} contains an oversized peer ID").into());
        }
        peers.insert(entry.parse::<PeerId>()?);
        if peers.len() > MAX_CONFIGURED_PEERS {
            return Err(format!("{setting} exceeds maximum peer count").into());
        }
    }
    if peers.is_empty() {
        return Err(format!("{setting} was set but contained no peer IDs").into());
    }
    Ok(Some(peers))
}

/// Optional per-node allowlist. Unset means open-network mode; set-but-empty is an error.
pub fn parse_trusted_peers(raw: Option<&str>) -> Result<Option<HashSet<PeerId>>, Box<dyn Error>> {
    parse_peer_set(raw, "OSIRIS_TRUSTED_PEERS")
}

/// Local operator-managed revocation list. It is evaluated before trust/allowlist acceptance.
pub fn parse_revoked_peers(raw: Option<&str>) -> Result<Option<HashSet<PeerId>>, Box<dyn Error>> {
    parse_peer_set(raw, "OSIRIS_REVOKED_PEERS")
}

pub fn run_if_peer_allowed<T>(
    peer: &PeerId,
    trusted_peers: &Option<HashSet<PeerId>>,
    revoked_peers: &Option<HashSet<PeerId>>,
    action: impl FnOnce() -> T,
) -> Result<T, ()> {
    if !is_peer_allowed(peer, trusted_peers, revoked_peers) {
        return Err(());
    }
    Ok(action())
}

pub fn is_peer_allowed(
    peer: &PeerId,
    trusted_peers: &Option<HashSet<PeerId>>,
    revoked_peers: &Option<HashSet<PeerId>>,
) -> bool {
    !revoked_peers.as_ref().is_some_and(|peers| peers.contains(peer))
        && trusted_peers.as_ref().is_none_or(|peers| peers.contains(peer))
}

#[cfg(test)]
mod tests {
    use super::{is_peer_allowed, parse_revoked_peers, parse_trusted_peers, run_if_peer_allowed};
    use libp2p::PeerId;

    #[test]
    fn unset_allowlist_preserves_open_mode() {
        assert!(parse_trusted_peers(None).unwrap().is_none());
        assert!(is_peer_allowed(&PeerId::random(), &None, &None));
    }

    #[test]
    fn parses_and_deduplicates_trusted_peer_ids() {
        let peer = PeerId::random();
        let raw = format!("{peer}, {peer}");
        let parsed = parse_trusted_peers(Some(&raw)).unwrap().unwrap();
        assert_eq!(parsed.len(), 1);
        assert!(parsed.contains(&peer));
    }

    #[test]
    fn rejects_empty_configured_allowlist() {
        assert!(parse_trusted_peers(Some(" , ")).is_err());
    }

    #[test]
    fn rejects_partially_malformed_allowlist_without_silently_ignoring_entries() {
        let peer = PeerId::random();
        for raw in [format!("{peer},"), format!(",{peer}"), format!("{peer}, ,{peer}")] {
            assert!(parse_trusted_peers(Some(&raw)).is_err());
            assert!(parse_revoked_peers(Some(&raw)).is_err());
        }
    }

    #[test]
    fn rejects_invalid_peer_id() {
        assert!(parse_trusted_peers(Some("not-a-peer-id")).is_err());
    }

    #[test]
    fn malformed_separators_fail_closed_for_both_peer_sets() {
        let peer = PeerId::random();
        for raw in ["", " ", ",", ",,", " , , "] {
            assert!(parse_trusted_peers(Some(raw)).is_err());
            assert!(parse_revoked_peers(Some(raw)).is_err());
        }
        for raw in [format!("{peer},,"), format!(",,{peer}")] {
            assert!(parse_trusted_peers(Some(&raw)).is_err());
            assert!(parse_revoked_peers(Some(&raw)).is_err());
        }
    }

    #[test]
    fn revocation_blocks_peer_even_in_open_network_mode() {
        let peer = PeerId::random();
        let revoked = Some([peer].into_iter().collect());
        assert!(!is_peer_allowed(&peer, &None, &revoked));
        let mut entered = false;
        assert!(run_if_peer_allowed(&peer, &None, &revoked, || { entered = true; }).is_err());
        assert!(!entered);
    }

    #[test]
    fn authorization_denies_revoked_peer_without_executing_callback() {
        use std::cell::Cell;
        let peer = PeerId::random();
        let revoked = Some([peer].into_iter().collect());
        let calls = Cell::new(0);
        for _ in 0..3 {
            assert!(run_if_peer_allowed(&peer, &None, &revoked, || {
                calls.set(calls.get() + 1);
            }).is_err());
        }
        assert_eq!(calls.get(), 0);
    }

    #[test]
    fn authorization_allows_only_configured_peer_and_executes_once() {
        use std::cell::Cell;
        let allowed = PeerId::random();
        let other = PeerId::random();
        let trusted = Some([allowed].into_iter().collect());
        let calls = Cell::new(0);
        assert!(run_if_peer_allowed(&other, &trusted, &None, || {
            calls.set(calls.get() + 1);
        }).is_err());
        assert_eq!(calls.get(), 0);
        assert!(run_if_peer_allowed(&allowed, &trusted, &None, || {
            calls.set(calls.get() + 1);
        }).is_ok());
        assert_eq!(calls.get(), 1);
    }

    #[test]
    fn rejects_unknown_peer() {
        let allowed = PeerId::random();
        let unknown = PeerId::random();
        let peers = Some([allowed].into_iter().collect());
        assert!(!is_peer_allowed(&unknown, &peers, &None));
    }

    #[test]
    fn revoked_peer_is_rejected_even_when_allowlisted() {
        let peer = PeerId::random();
        let trusted = Some([peer].into_iter().collect());
        let revoked = Some([peer].into_iter().collect());
        assert!(!is_peer_allowed(&peer, &trusted, &revoked));
    }

    #[test]
    fn revoked_allowlisted_peer_never_enters_encrypted_message_processing() {
        use std::cell::Cell;
        let peer = PeerId::random();
        let trusted = Some([peer].into_iter().collect());
        let revoked = Some([peer].into_iter().collect());
        let processing_started = Cell::new(false);

        let result = run_if_peer_allowed(&peer, &trusted, &revoked, || {
            processing_started.set(true);
            // Production supplies envelope parsing here; key lookup, decryption,
            // and replay handling are downstream of this authorization gate.
            "parsed"
        });

        assert!(result.is_err());
        assert!(!processing_started.get());
    }

    #[test]
    fn allowed_peer_enters_encrypted_message_processing() {
        let peer = PeerId::random();
        let trusted = Some([peer].into_iter().collect());
        let result = run_if_peer_allowed(&peer, &trusted, &None, || "parsed");
        assert_eq!(result, Ok("parsed"));
    }

    #[test]
    fn parses_and_deduplicates_revoked_peer_ids() {
        let peer = PeerId::random();
        let raw = format!("{peer}, {peer}");
        let parsed = parse_revoked_peers(Some(&raw)).unwrap().unwrap();
        assert_eq!(parsed.len(), 1);
        assert!(parsed.contains(&peer));
    }

    #[test]
    fn rejects_empty_configured_revocation_list() {
        assert!(parse_revoked_peers(Some(" , ")).is_err());
    }

    #[test]
    fn absent_or_valid_utf8_settings_preserve_their_meaning() {
        assert!(super::validate_peer_setting(None, "OSIRIS_TRUSTED_PEERS").unwrap().is_none());
        assert_eq!(super::validate_peer_setting(Some("".into()), "OSIRIS_TRUSTED_PEERS").unwrap(), Some(String::new()));
    }

    #[cfg(unix)]
    #[test]
    fn invalid_utf8_peer_configuration_fails_closed_without_exposing_value() {
        use std::{ffi::OsString, os::unix::ffi::OsStringExt};
        for setting in ["OSIRIS_TRUSTED_PEERS", "OSIRIS_REVOKED_PEERS"] {
            let value = OsString::from_vec(b"private-config-\xff".to_vec());
            let error = super::validate_peer_setting(Some(value), setting).unwrap_err().to_string();
            assert!(error.contains(setting));
            assert!(!error.contains("private-config"));
        }
    }

    #[test]
    fn rejects_oversized_peer_configuration_before_parsing() {
        let oversized_id = "1".repeat(super::MAX_PEER_ID_BYTES + 1);
        assert!(parse_trusted_peers(Some(&oversized_id)).unwrap_err().to_string().contains("oversized"));
        let oversized_config = " ".repeat(super::MAX_PEER_CONFIG_BYTES + 1);
        assert!(parse_revoked_peers(Some(&oversized_config)).unwrap_err().to_string().contains("configuration size"));
    }
}
