//! Public Binance trade-stream observer. No orders, private gateways or stealth claims.
//! Requires network access; timestamps are local receipt timestamps, not latency guarantees.
use futures::StreamExt;
use serde_json::Value;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio_tungstenite::{connect_async, tungstenite::Message};

const STREAMS: &[&str] = &["btcusdt", "ethusdt", "solusdt", "bnbusdt"];

fn trade_fields(message: &str) -> Option<(String, String, u64)> {
    let envelope: Value = serde_json::from_str(message).ok()?;
    let trade = envelope.get("data")?;
    if trade.get("e")?.as_str()? != "trade" {
        return None;
    }
    let symbol = trade.get("s")?.as_str()?;
    let price = trade.get("p")?.as_str()?;
    let exchange_ms = trade.get("T")?.as_u64()?;
    let parsed_price = price.parse::<f64>().ok()?;
    if symbol.is_empty() || !parsed_price.is_finite() || parsed_price <= 0.0 {
        return None;
    }
    Some((symbol.to_owned(), price.to_owned(), exchange_ms))
}

#[tokio::main]
async fn main() {
    let streams = STREAMS.iter().map(|symbol| format!("{symbol}@trade")).collect::<Vec<_>>().join("/");
    let endpoint = format!("wss://stream.binance.com:9443/stream?streams={streams}");
    let mut retry = 1_u64;
    loop {
        match connect_async(&endpoint).await {
            Ok((socket, _)) => {
                eprintln!("Connected to public Binance trade stream");
                retry = 1;
                let (_, mut incoming) = socket.split();
                while let Some(result) = incoming.next().await {
                    match result {
                        Ok(Message::Text(text)) => {
                            if let Some((symbol, price, exchange_ms)) = trade_fields(&text) {
                                let received_ns = SystemTime::now().duration_since(UNIX_EPOCH)
                                    .map(|duration| duration.as_nanos()).unwrap_or_default();
                                println!("{symbol} price={price} exchange_ms={exchange_ms} received_ns={received_ns}");
                            }
                        }
                        Ok(Message::Close(_)) => break,
                        Ok(_) => {},
                        Err(error) => {
                            eprintln!("Trade stream interrupted: {error}");
                            break;
                        }
                    }
                }
            }
            Err(error) => eprintln!("Public trade stream unavailable: {error}"),
        }
        tokio::time::sleep(Duration::from_secs(retry)).await;
        retry = (retry * 2).min(60);
    }
}

#[cfg(test)]
mod tests {
    use super::trade_fields;
    #[test]
    fn accepts_combined_trade() {
        let payload = r#"{"stream":"btcusdt@trade","data":{"e":"trade","s":"BTCUSDT","p":"90000.1","T":1234}}"#;
        assert_eq!(trade_fields(payload), Some(("BTCUSDT".into(), "90000.1".into(), 1234)));
    }
    #[test]
    fn rejects_invalid_and_non_trade() {
        assert!(trade_fields("{}").is_none());
        assert!(trade_fields(r#"{"data":{"e":"depthUpdate","s":"BTCUSDT","p":"1","T":1}}"#).is_none());
        assert!(trade_fields(r#"{"data":{"e":"trade","s":"BTCUSDT","p":"NaN","T":1}}"#).is_none());
    }
}
