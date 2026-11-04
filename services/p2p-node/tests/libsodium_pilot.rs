//! Opt-in libsodium compatibility pilot; not wired into production E2EE.
//! Run: cargo test --features libsodium-pilot --test libsodium_pilot
#![cfg(feature = "libsodium-pilot")]

use sodiumoxide::crypto::secretbox;

#[test]
fn libsodium_secretbox_round_trip_and_authentication() {
    assert!(sodiumoxide::init().is_ok(), "libsodium initialization failed");
    let key = secretbox::gen_key();
    let nonce = secretbox::gen_nonce();
    let plaintext = b"osiris-libsodium-pilot";
    let ciphertext = secretbox::seal(plaintext, &nonce, &key);
    assert_eq!(secretbox::open(&ciphertext, &nonce, &key).unwrap(), plaintext);

    let mut tampered = ciphertext.clone();
    tampered[0] ^= 0x01;
    assert!(secretbox::open(&tampered, &nonce, &key).is_err());

    let other_key = secretbox::gen_key();
    assert!(secretbox::open(&ciphertext, &nonce, &other_key).is_err());
}

#[test]
fn libsodium_nonce_uniqueness_smoke_test() {
    assert!(sodiumoxide::init().is_ok());
    let a = secretbox::gen_nonce();
    let b = secretbox::gen_nonce();
    assert_ne!(a, b);
}
