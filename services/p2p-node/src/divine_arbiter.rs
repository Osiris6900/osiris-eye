//! Read-only market anomaly alerts based on public Binance PEPEUSDT trades.
//! An anomaly is not evidence of manipulation; no automatic order controls.
use futures::StreamExt;
use serde_json::Value;
use std::{collections::VecDeque, time::{Duration, Instant}};
use tokio_tungstenite::{connect_async, tungstenite::Message};

#[derive(Clone, Copy)]
struct Trade { price: f64, quantity: f64, seen: Instant }
fn parse_trade(raw: &str) -> Option<Trade> {
    let v: Value = serde_json::from_str(raw).ok()?;
    if v.get("e")?.as_str()? != "trade" || v.get("s")?.as_str()? != "PEPEUSDT" { return None; }
    let price = v.get("p")?.as_str()?.parse::<f64>().ok()?;
    let quantity = v.get("q")?.as_str()?.parse::<f64>().ok()?;
    if !price.is_finite() || price <= 0.0 || !quantity.is_finite() || quantity < 0.0 { return None; }
    Some(Trade { price, quantity, seen: Instant::now() })
}
struct Arbiter { history: VecDeque<Trade>, last_alert: Option<Instant> }
impl Arbiter {
    fn new() -> Self { Self { history: VecDeque::new(), last_alert: None } }
    fn observe(&mut self, trade: Trade) -> Option<String> {
        self.history.push_back(trade);
        while self.history.len() > 3000 ||
            self.history.front().map(|t| trade.seen.duration_since(t.seen) > Duration::from_secs(60)).unwrap_or(false) {
            self.history.pop_front();
        }
        if self.history.len() < 30 || self.last_alert.map(|t| t.elapsed() < Duration::from_secs(30)).unwrap_or(false) { return None; }
        let first = self.history.front()?;
        let change_pct = 100.0 * (trade.price / first.price - 1.0);
        let avg_qty = self.history.iter().map(|t| t.quantity).sum::<f64>() / self.history.len() as f64;
        let anomaly = if change_pct <= -5.0 {
            Some(format!("CRITICAL: PEPEUSDT observed {:.2}% decline in rolling sample; review market risk", change_pct))
        } else if avg_qty > 0.0 && trade.quantity >= avg_qty * 10.0 {
            Some(format!("WARNING: PEPEUSDT trade quantity {:.2} is >=10x sample average {:.2}; review liquidity", trade.quantity, avg_qty))
        } else { None };
        if anomaly.is_some() { self.last_alert = Some(trade.seen); }
        anomaly
    }
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("OSIRIS Divine Arbiter — read-only PEPEUSDT market anomaly observer");
    let mut delay = 2u64;
    loop {
        match tokio::time::timeout(Duration::from_secs(15), connect_async("wss://stream.binance.com:9443/ws/pepeusdt@trade")).await {
            Ok(Ok((socket, _))) => {
                delay = 2;
                let (_, mut reader) = socket.split();
                let mut arbiter = Arbiter::new();
                loop {
                    tokio::select! {
                        _ = tokio::signal::ctrl_c() => return Ok(()),
                        result = tokio::time::timeout(Duration::from_secs(30), reader.next()) => {
                            match result {
                                Ok(Some(Ok(Message::Text(raw)))) => {
                                    if let Some(trade) = parse_trade(&raw) {
                                        if let Some(alert) = arbiter.observe(trade) { println!("{alert}"); }
                                    }
                                }
                                Ok(Some(Ok(Message::Close(_)))) | Ok(None) | Ok(Some(Err(_))) | Err(_) => break,
                                Ok(Some(Ok(_))) => {}
                            }
                        }
                    }
                }
            }
            Ok(Err(e)) => eprintln!("market feed connection error: {e}"),
            Err(_) => eprintln!("market feed connection timeout"),
        }
        tokio::select! {
            _ = tokio::signal::ctrl_c() => return Ok(()),
            _ = tokio::time::sleep(Duration::from_secs(delay)) => {}
        }
        delay = (delay * 2).min(60);
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn validates_string_trade_fields() {
        assert!(parse_trade(r#"{"e":"trade","s":"PEPEUSDT","p":"0.00001","q":"1000"}"#).is_some());
        assert!(parse_trade(r#"{"e":"trade","s":"PEPEUSDT","p":"NaN","q":"1000"}"#).is_none());
        assert!(parse_trade(r#"{"e":"trade","s":"BTCUSDT","p":"1","q":"1000"}"#).is_none());
    }
    #[test]
    fn detects_rapid_drop() {
        let mut arbiter = Arbiter::new();
        let start = Instant::now();
        for i in 0..30 {
            let t = Trade { price: if i == 29 { 0.94 } else { 1.0 }, quantity: 1.0, seen: start + Duration::from_millis(i*100) };
            let result = arbiter.observe(t);
            if i == 29 { assert!(result.unwrap().contains("CRITICAL")); }
        }
    }
}
