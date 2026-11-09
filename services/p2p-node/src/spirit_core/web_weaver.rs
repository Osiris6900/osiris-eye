//! Top-of-book liquidity anomaly observer. A disappearing level is NOT proof of spoofing.
use futures::StreamExt;
use serde_json::Value;
use std::{collections::HashMap, time::{Duration, Instant}};
use tokio_tungstenite::{connect_async, tungstenite::Message};

const LARGE_QTY: f64 = 50.0;
const FAST_DISAPPEAR: Duration = Duration::from_millis(500);
const MAX_TRACKED: usize = 40;

#[derive(Clone, Debug)]
struct Wall { first_seen: Instant, quantity: f64 }
#[derive(Default)]
struct Tracker { bids: HashMap<String, Wall>, asks: HashMap<String, Wall> }
#[derive(Debug, PartialEq)]
struct Observation { side: &'static str, price: String, elapsed_ms: u128 }

fn levels<'a>(value: &'a Value, side: &str) -> Option<&'a Vec<Value>> {
    value.get("data").unwrap_or(value).get(side)?.as_array()
}
fn parse_level(level: &Value) -> Option<(String, f64)> {
    let parts = level.as_array()?;
    let price = parts.first()?.as_str()?.to_string();
    let qty = parts.get(1)?.as_str()?.parse::<f64>().ok()?;
    let p = price.parse::<f64>().ok()?;
    if !p.is_finite() || p <= 0.0 || !qty.is_finite() || qty < 0.0 { return None; }
    Some((price, qty))
}
fn observe_side(
    tracked: &mut HashMap<String, Wall>, book: &[Value], side: &'static str, now: Instant,
) -> Vec<Observation> {
    let current: HashMap<String, f64> = book.iter().filter_map(parse_level).collect();
    let mut observations = Vec::new();
    tracked.retain(|price, wall| {
        if !current.contains_key(price) {
            let age = now.duration_since(wall.first_seen);
            if age < FAST_DISAPPEAR {
                observations.push(Observation { side, price: price.clone(), elapsed_ms: age.as_millis() });
            }
            false
        } else { true }
    });
    for (price, qty) in current {
        if qty >= LARGE_QTY && tracked.len() < MAX_TRACKED {
            tracked.entry(price).or_insert(Wall { first_seen: now, quantity: qty });
        }
    }
    observations
}
fn process(tracker: &mut Tracker, payload: &str, now: Instant) -> Option<Vec<Observation>> {
    let value: Value = serde_json::from_str(payload).ok()?;
    let bids = levels(&value, "bids")?;
    let asks = levels(&value, "asks")?;
    if bids.len() > 20 || asks.len() > 20 { return None; }
    let mut output = observe_side(&mut tracker.bids, bids, "bid", now);
    output.extend(observe_side(&mut tracker.asks, asks, "ask", now));
    Some(output)
}
#[tokio::main]
async fn main() {
    const ENDPOINT: &str = "wss://stream.binance.com:9443/stream?streams=btcusdt@depth20@100ms";
    let mut retry = 2_u64;
    loop {
        match tokio::time::timeout(Duration::from_secs(15), connect_async(ENDPOINT)).await {
            Ok(Ok((socket, _))) => {
                retry = 2;
                // Reset tracking on reconnect: snapshots across a disconnect are not comparable.
                let mut tracker = Tracker::default();
                let (_, mut read) = socket.split();
                loop {
                    match tokio::time::timeout(Duration::from_secs(30), read.next()).await {
                        Ok(Some(Ok(Message::Text(text)))) => {
                            if let Some(events) = process(&mut tracker, &text, Instant::now()) {
                                for event in events {
                                    println!("liquidity_change side={} price={} elapsed_ms={} (not spoofing proof)",
                                        event.side, event.price, event.elapsed_ms);
                                }
                            }
                        }
                        Ok(Some(Ok(Message::Close(_)))) | Ok(None) | Ok(Some(Err(_))) | Err(_) => break,
                        Ok(Some(Ok(_))) => {}
                    }
                }
            }
            Ok(Err(error)) => eprintln!("depth connection failed: {error}"),
            Err(_) => eprintln!("depth connection timeout"),
        }
        tokio::time::sleep(Duration::from_secs(retry)).await;
        retry = (retry * 2).min(60);
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn detects_fast_disappearance_without_claiming_spoofing() {
        let mut tracker = Tracker::default();
        let now = Instant::now();
        let first = r#"{"data":{"bids":[["90000","55"]],"asks":[]}}"#;
        assert!(process(&mut tracker, first, now).unwrap().is_empty());
        let second = r#"{"data":{"bids":[],"asks":[]}}"#;
        let changes = process(&mut tracker, second, now + Duration::from_millis(100)).unwrap();
        assert_eq!(changes, vec![Observation { side:"bid", price:"90000".into(), elapsed_ms:100 }]);
    }
    #[test]
    fn malformed_and_stale_are_not_alerts() {
        let mut tracker = Tracker::default();
        let now = Instant::now();
        assert!(process(&mut tracker, "{}", now).is_none());
        process(&mut tracker, r#"{"data":{"bids":[["1","51"]],"asks":[]}}"#, now);
        assert!(process(&mut tracker, r#"{"data":{"bids":[],"asks":[]}}"#, now + Duration::from_secs(1)).unwrap().is_empty());
    }
}
