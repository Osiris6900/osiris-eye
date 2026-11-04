use ed25519_dalek::{Signer, SigningKey};
use libp2p::{identity, PeerId};
use serde::Serialize;
use std::{env, error::Error, fs, path::PathBuf, time::{SystemTime, UNIX_EPOCH}};

#[derive(Serialize)]
struct Payload<'a> {
    schema: &'a str,
    authority_key: &'a str,
    peer_id: &'a str,
    sequence: u64,
    issued_at: u64,
    reason_code: &'a str,
}
#[derive(Serialize)]
struct Record {
    schema: String,
    authority_key: String,
    peer_id: String,
    sequence: u64,
    issued_at: u64,
    reason_code: String,
    signature: String,
}
fn hex(bytes: &[u8]) -> String { bytes.iter().map(|b| format!("{b:02x}")).collect() }
fn main() -> Result<(), Box<dyn Error>> {
    let mut args = env::args().skip(1);
    let path = PathBuf::from(args.next().ok_or("usage: make_revocation_fixture <record.json> [target-peer-id]")?);
    let key = SigningKey::from_bytes(&[42u8; 32]); // test-only authority; never use in production
    let authority_key = hex(key.verifying_key().as_bytes());
    let peer_id = match args.next() {
        Some(value) => value.parse::<PeerId>()?.to_string(),
        None => PeerId::from(identity::Keypair::generate_ed25519().public()).to_string(),
    };
    if args.next().is_some() { return Err("too many arguments".into()); }
    let issued_at = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    let payload = Payload { schema: "osiris.revocation.v1", authority_key: &authority_key,
        peer_id: &peer_id, sequence: 1, issued_at, reason_code: "integration_test" };
    let bytes = serde_json::to_vec(&payload)?;
    let record = Record { schema: payload.schema.into(), authority_key: authority_key.clone(),
        peer_id: peer_id.clone(), sequence: 1, issued_at, reason_code: payload.reason_code.into(),
        signature: hex(&key.sign(&bytes).to_bytes()) };
    fs::write(path, serde_json::to_vec_pretty(&vec![record])?)?;
    println!("authority_public_key={authority_key}");
    println!("target_peer_id={peer_id}");
    Ok(())
}
