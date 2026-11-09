//! Opt-in health checks for explicitly configured Osiris-owned endpoints.
//! No CIDR enumeration, certificate bypass, or third-party host scanning.
use std::{env, time::Duration};
use tokio::{io::{AsyncReadExt, AsyncWriteExt}, net::TcpStream, time::timeout};

const MAX_NODES: usize = 32;
const CONNECT_TIMEOUT: Duration = Duration::from_millis(750);

fn parse_targets(input: &str) -> Result<Vec<(String, u16)>, String> {
    let mut targets = Vec::new();
    for entry in input.split(',').map(str::trim).filter(|x| !x.is_empty()) {
        if targets.len() >= MAX_NODES { return Err("too many nodes (max 32)".into()); }
        let (host, port) = entry.rsplit_once(':').ok_or("expected host:port")?;
        let port: u16 = port.parse().map_err(|_| "invalid port")?;
        if port == 0 || host.is_empty() || host.contains('/') || host.contains('@') ||
           host.contains(' ') || host.contains('[') || host.contains(']') {
            return Err("invalid host:port".into());
        }
        // Require an explicitly supplied target; no implicit subnet expansion.
        targets.push((host.to_owned(), port));
    }
    if targets.is_empty() { return Err("OSIRIS_NODE_TARGETS is empty".into()); }
    Ok(targets)
}
async fn probe(host: &str, port: u16) -> bool {
    matches!(timeout(CONNECT_TIMEOUT, TcpStream::connect((host, port))).await, Ok(Ok(_)))
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let raw = env::var("OSIRIS_NODE_TARGETS")
        .map_err(|_| "set OSIRIS_NODE_TARGETS to authorized host:port pairs")?;
    let targets = parse_targets(&raw)?;
    println!("Void Walker: checking {} explicitly authorized endpoints", targets.len());
    for (host, port) in targets {
        let reachable = probe(&host, port).await;
        println!("node={host}:{port} tcp_reachable={reachable}");
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn accepts_explicit_targets() {
        assert_eq!(parse_targets("127.0.0.1:4001,localhost:4002").unwrap().len(), 2);
    }
    #[test]
    fn rejects_cidr_and_unbounded_lists() {
        assert!(parse_targets("104.16.0.0/12:80").is_err());
        assert!(parse_targets("example.com:0").is_err());
        assert!(parse_targets(&vec!["localhost:80"; 33].join(",")).is_err());
    }
}
