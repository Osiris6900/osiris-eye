use std::{collections::HashSet, error::Error};
use libp2p::PeerId;

fn parse_peer_set(raw: Option<&str>, setting: &str) -> Result<Option<HashSet<PeerId>>, Box<dyn Error>> {
    let Some(raw) = raw else { return Ok(None); };
    let mut peers = HashSet::new();
    for entry in raw.split(',').map(str::trim).filter(|entry| !entry.is_empty()) {
        peers.insert(entry.parse::<PeerId>()?);
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
    use super::{is_peer_allowed, parse_revoked_peers, parse_trusted_peers};
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
    fn rejects_invalid_peer_id() {
        assert!(parse_trusted_peers(Some("not-a-peer-id")).is_err());
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
}