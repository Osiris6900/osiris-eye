//! Concurrent public-market observer. No private endpoint access or trade execution.
use futures::StreamExt;
use serde_json::Value;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio_tungstenite::{connect_async, tungstenite::Message};

fn parse_price_safe(value: &Value) -> Option<f64> {
    let price = value.as_str().and_then(|s| s.parse::<f64>().ok()).or_else(|| value.as_f64())?;
    (price.is_finite() && price > 0.0).then_some(price)
}

fn best_bid(payload: &str) -> Option<f64> {
    let json: Value = serde_json::from_str(payload).ok()?;
    parse_price_safe(json.get("data")?.get("bids")?.as_array()?.first()?.as_array()?.first()?)
}

fn receipt_ns() -> u128 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or_default()
}

async fn crypto_depth() {
    const ENDPOINT: &str = "wss://stream.binance.com:9443/stream?streams=btcusdt@depth20@100ms";
    let mut delay = 2_u64;
    loop {
        match tokio::time::timeout(Duration::from_secs(15), connect_async(ENDPOINT)).await {
            Ok(Ok((stream, _))) => {
                delay = 2;
                let (_, mut reader) = stream.split();
                loop {
                    match tokio::time::timeout(Duration::from_secs(45), reader.next()).await {
                        Ok(Some(Ok(Message::Text(text)))) => {
                            if let Some(price) = best_bid(&text) {
                                println!("BTCUSDT best_bid={price} received_ns={}", receipt_ns());
                            }
                        }
                        Ok(Some(Ok(Message::Close(_)))) | Ok(None) => break,
                        Ok(Some(Err(error))) => { eprintln!("depth socket error: {error}"); break; }
                        Err(_) => { eprintln!("depth socket timed out"); break; }
                        Ok(Some(Ok(_))) => {}
                    }
                }
            }
            Ok(Err(error)) => eprintln!("public depth connection failed: {error}"),
            Err(_) => eprintln!("public depth connection timed out"),
        }
        tokio::time::sleep(Duration::from_secs(delay)).await;
        delay = (delay * 2).min(60);
    }
}

#[tokio::main]
async fn main() {
    eprintln!("Osiris Vampire Core: public market observer, not stealth or order execution");
    let crypto = tokio::spawn(crypto_depth());
    tokio::select! {
        _ = tokio::signal::ctrl_c() => eprintln!("Shutdown requested"),
        result = crypto => eprintln!("Crypto task ended unexpectedly: {result:?}"),
    }
}

#[cfg(test)]
mod tests {
    use super::{best_bid, parse_price_safe};
    use serde_json::json;
    #[test]
    fn parses_string_and_numeric_prices() {
        assert_eq!(parse_price_safe(&json!("123.45")), Some(123.45));
        assert_eq!(parse_price_safe(&json!(123.45)), Some(123.45));
        assert_eq!(parse_price_safe(&json!("NaN")), None);
        assert_eq!(parse_price_safe(&json!(-1)), None);
    }
    #[test]
    fn parses_combined_depth_message() {
        assert_eq!(best_bid(r#"{"data":{"bids":[["90000.1","2"]],"asks":[]}}"#), Some(90000.1));
        assert_eq!(best_bid("{}"), None);
        assert_eq!(best_bid(r#"{"data":{"bids":[]}}"#), None);
    }
}
