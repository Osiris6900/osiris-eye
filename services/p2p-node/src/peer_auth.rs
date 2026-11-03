use std::{collections::HashSet, error::Error};
use libp2p::PeerId;

/// Optional per-node allowlist. Unset means open-network mode; set-but-empty is an error.
pub fn parse_trusted_peers(raw: Option<&str>) -> Result<Option<HashSet<PeerId>>, Box<dyn Error>> {
    let Some(raw) = raw else { return Ok(None); };
    let mut peers = HashSet::new();
    for entry in raw.split(',').map(str::trim).filter(|entry| !entry.is_empty()) {
        peers.insert(entry.parse::<PeerId>()?);
    }
    if peers.is_empty() {
        return Err("OSIRIS_TRUSTED_PEERS was set but contained no peer IDs".into());
    }
    Ok(Some(peers))
}

pub fn is_trusted_peer(peer: &PeerId, trusted_peers: &Option<HashSet<PeerId>>) -> bool {
    trusted_peers.as_ref().is_none_or(|peers| peers.contains(peer))
}

#[cfg(test)]
mod tests {
    use super::{is_trusted_peer, parse_trusted_peers};
    use libp2p::PeerId;

    #[test]
    fn unset_allowlist_preserves_open_mode() {
        assert!(parse_trusted_peers(None).unwrap().is_none());
        assert!(is_trusted_peer(&PeerId::random(), &None));
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
        assert!(!is_trusted_peer(&unknown, &peers));
    }
}
