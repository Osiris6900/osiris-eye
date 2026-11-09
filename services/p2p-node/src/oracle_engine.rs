//! Cross-venue public trade-price discrepancy monitor. Signals are NOT executable arbitrage.
use futures::StreamExt;
use serde_json::Value;
use std::{collections::HashMap, time::{Duration, Instant}};
use tokio::{sync::mpsc, time::timeout};
use tokio_tungstenite::{connect_async, tungstenite::Message};

const THRESHOLD_PCT: f64 = 0.05;
const MAX_AGE: Duration = Duration::from_secs(3);
const QUEUE_SIZE: usize = 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
enum Venue { Global, Us }
impl Venue {
    fn label(self) -> &'static str { match self { Self::Global => "GLOBAL", Self::Us => "US" } }
    fn endpoint(self) -> &'static str {
        match self {
            Self::Global => "wss://stream.binance.com:9443/ws/btcusdt@trade",
            Self::Us => "wss://stream.binance.us:9443/ws/btcusdt@trade",
        }
    }
}
#[derive(Debug)]
struct Tick { venue: Venue, symbol: String, price: f64, received: Instant }
fn parse_tick(venue: Venue, raw: &str) -> Option<Tick> {
    let json: Value = serde_json::from_str(raw).ok()?;
    if json.get("e")?.as_str()? != "trade" { return None; }
    let symbol = json.get("s")?.as_str()?.to_string();
    let price = json.get("p")?.as_str()?.parse::<f64>().ok()?;
    if symbol != "BTCUSDT" || !price.is_finite() || price <= 0.0 { return None; }
    Some(Tick { venue, symbol, price, received: Instant::now() })
}
fn discrepancy(state: &HashMap<Venue, Tick>, now: Instant) -> Option<f64> {
    let global = state.get(&Venue::Global)?;
    let us = state.get(&Venue::Us)?;
    if global.symbol != us.symbol || now.duration_since(global.received) > MAX_AGE ||
       now.duration_since(us.received) > MAX_AGE { return None; }
    Some((global.price - us.price) / us.price * 100.0)
}
async fn listen(venue: Venue, tx: mpsc::Sender<Tick>) {
    let mut retry = 2_u64;
    loop {
        match timeout(Duration::from_secs(15), connect_async(venue.endpoint())).await {
            Ok(Ok((socket, _))) => {
                retry = 2;
                let (_, mut reader) = socket.split();
                loop {
                    match timeout(Duration::from_secs(30), reader.next()).await {
                        Ok(Some(Ok(Message::Text(raw)))) => {
                            if let Some(tick) = parse_tick(venue, &raw) {
                                if tx.send(tick).await.is_err() { return; }
                            }
                        }
                        Ok(Some(Ok(Message::Close(_)))) | Ok(None) | Err(_) | Ok(Some(Err(_))) => break,
                        Ok(Some(Ok(_))) => {}
                    }
                }
            }
            Ok(Err(error)) => eprintln!("{} feed error: {error}", venue.label()),
            Err(_) => eprintln!("{} feed connection timeout", venue.label()),
        }
        tokio::time::sleep(Duration::from_secs(retry)).await;
        retry = (retry * 2).min(60);
    }
}
#[tokio::main]
async fn main() {
    let (tx, mut rx) = mpsc::channel(QUEUE_SIZE);
    let a = tokio::spawn(listen(Venue::Global, tx.clone()));
    let b = tokio::spawn(listen(Venue::Us, tx));
    let mut state = HashMap::new();
    println!("Osiris Oracle: public cross-venue trade monitor (no orders)");
    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => break,
            tick = rx.recv() => match tick {
                Some(tick) => {
                    state.insert(tick.venue, tick);
                    if let Some(gap) = discrepancy(&state, Instant::now()) {
                        if gap.abs() >= THRESHOLD_PCT {
                            println!("BTCUSDT venue trade-price difference={gap:.4}% (not net arbitrage)");
                        }
                    }
                }
                None => break,
            }
        }
    }
    a.abort();
    b.abort();
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_bad_trade_data() {
        assert!(parse_tick(Venue::Global, "{}").is_none());
        assert!(parse_tick(Venue::Global, r#"{"e":"trade","s":"BTCUSDT","p":"NaN"}"#).is_none());
        assert!(parse_tick(Venue::Global, r#"{"e":"trade","s":"BTCUSDT","p":"100"}"#).is_some());
    }
    #[test]
    fn only_compares_fresh_distinct_venues() {
        let now = Instant::now();
        let mut state = HashMap::new();
        state.insert(Venue::Global, Tick { venue: Venue::Global, symbol: "BTCUSDT".into(), price: 101.0, received: now });
        assert!(discrepancy(&state, now).is_none());
        state.insert(Venue::Us, Tick { venue: Venue::Us, symbol: "BTCUSDT".into(), price: 100.0, received: now });
        assert_eq!(discrepancy(&state, now), Some(1.0));
        assert!(discrepancy(&state, now + Duration::from_secs(4)).is_none());
    }
}
