//! Offline-first headline sentiment monitor.
//! Reads one JSON news item per stdin line from a licensed/authorized news adapter.
//! No unauthenticated scraping, and no trading signals are emitted.
use serde_json::Value;
use std::collections::HashSet;
use tokio::io::{self, AsyncBufReadExt, BufReader};

const THRESHOLD: i32 = 3;
const BULLISH: &[&str] = &["moon", "pump", "breakout", "bullish", "surge", "adoption"];
const BEARISH: &[&str] = &["crash", "dump", "ban", "hack", "bearish", "regulation", "scam"];

#[derive(Debug, PartialEq)]
struct Sentiment { score: i32, keywords: Vec<String> }
fn analyze(text: &str) -> Sentiment {
    // Match whole ASCII-alphanumeric tokens, not substrings like 'banner' => 'ban'.
    let words: HashSet<String> = text.split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|s| !s.is_empty()).map(|s| s.to_ascii_lowercase()).collect();
    let mut score = 0;
    let mut keywords = Vec::new();
    for word in BULLISH {
        if words.contains(*word) { score += 1; keywords.push((*word).into()); }
    }
    for word in BEARISH {
        if words.contains(*word) { score -= 1; keywords.push((*word).into()); }
    }
    Sentiment { score: score.clamp(-10, 10), keywords }
}
fn headline(json: &Value) -> Option<&str> {
    json.get("title").and_then(Value::as_str)
        .or_else(|| json.get("headline").and_then(Value::as_str))
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("OSIRIS HIVE MIND: reading authorized JSON headlines from stdin");
    let mut lines = BufReader::new(io::stdin()).lines();
    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => break,
            line = lines.next_line() => {
                let Some(line) = line? else { break };
                if line.len() > 16 * 1024 { eprintln!("headline line too long"); continue; }
                let Ok(value) = serde_json::from_str::<Value>(&line) else {
                    eprintln!("invalid JSON headline"); continue;
                };
                let Some(title) = headline(&value) else { continue };
                let result = analyze(title);
                if result.score.abs() >= THRESHOLD {
                    println!("headline sentiment score={} keywords={:?} (lexicon heuristic, not a forecast)",
                        result.score, result.keywords);
                }
            }
        }
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn handles_positive_and_negative_headlines() {
        assert_eq!(analyze("Bullish breakout surge adoption").score, 4);
        assert_eq!(analyze("Crash dump hack scam").score, -4);
    }
    #[test]
    fn matches_words_not_substrings_and_deduplicates() {
        assert_eq!(analyze("banner crashing moon moon").score, 1);
        assert_eq!(analyze("REGULATION! ban").score, -2);
    }
    #[test]
    fn rejects_unstructured_news() {
        assert_eq!(headline(&serde_json::json!({"title":"Bullish"})), Some("Bullish"));
        assert_eq!(headline(&serde_json::json!({"other":"data"})), None);
    }
}
