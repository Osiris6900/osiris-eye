//! Optional native desktop market monitor. Does not replace the P2P node.
#[cfg(feature = "angel-desktop")]
mod desktop {
    use eframe::egui;
    use futures::StreamExt;
    use serde_json::Value;
    use std::{sync::{Arc, Mutex}, time::{Duration, Instant}};
    use tokio_tungstenite::{connect_async, tungstenite::Message};

    #[derive(Clone)]
    struct MarketState {
        price: Option<f64>,
        quantity: Option<f64>,
        connected: bool,
        last_trade: Option<Instant>,
    }
    impl Default for MarketState {
        fn default() -> Self {
            Self { price: None, quantity: None, connected: false, last_trade: None }
        }
    }
    fn parse_trade(text: &str) -> Option<(f64, f64)> {
        let v: Value = serde_json::from_str(text).ok()?;
        if v.get("e")?.as_str()? != "trade" || v.get("s")?.as_str()? != "BTCUSDT" { return None; }
        let price = v.get("p")?.as_str()?.parse::<f64>().ok()?;
        let quantity = v.get("q")?.as_str()?.parse::<f64>().ok()?;
        (price.is_finite() && price > 0.0 && quantity.is_finite() && quantity >= 0.0)
            .then_some((price, quantity))
    }
    struct AngelApp { state: Arc<Mutex<MarketState>> }
    impl eframe::App for AngelApp {
        fn update(&mut self, ctx: &egui::Context, _: &mut eframe::Frame) {
            let state = self.state.lock().map(|s| s.clone()).unwrap_or_default();
            egui::CentralPanel::default().show(ctx, |ui| {
                ui.vertical_centered(|ui| {
                    ui.heading(egui::RichText::new("OSIRIS EYE · ANGEL INTERFACE").color(egui::Color32::LIGHT_BLUE));
                    ui.add_space(24.0);
                });
                let fresh = state.last_trade.map(|t| t.elapsed() < Duration::from_secs(10)).unwrap_or(false);
                ui.label(if state.connected && fresh { "Public trade feed: LIVE" } else if state.connected { "Public trade feed: STALE" } else { "Public trade feed: DISCONNECTED" });
                ui.separator();
                ui.heading("BTCUSDT last trade");
                ui.label(state.price.filter(|_| fresh).map(|p| format!("USD {p:.2}")).unwrap_or_else(|| "Unavailable".to_string()));
                ui.heading("Last trade quantity (BTC)");
                ui.label(state.quantity.filter(|_| fresh).map(|q| format!("{q:.6}")).unwrap_or_else(|| "Unavailable".to_string()));
                ui.separator();
                ui.small("Read-only public feed · no orders · no P2P peer telemetry");
            });
            ctx.request_repaint_after(Duration::from_millis(200));
        }
    }
    async fn stream(state: Arc<Mutex<MarketState>>) {
        let mut backoff = 2_u64;
        loop {
            match tokio::time::timeout(Duration::from_secs(15), connect_async("wss://stream.binance.com:9443/ws/btcusdt@trade")).await {
                Ok(Ok((socket, _))) => {
                    backoff = 2;
                    if let Ok(mut s) = state.lock() { s.connected = true; }
                    let (_, mut reader) = socket.split();
                    loop {
                        match tokio::time::timeout(Duration::from_secs(30), reader.next()).await {
                            Ok(Some(Ok(Message::Text(t)))) => {
                                if let Some((price, quantity)) = parse_trade(&t) {
                                    if let Ok(mut s) = state.lock() {
                                        s.price = Some(price);
                                        s.quantity = Some(quantity);
                                        s.last_trade = Some(Instant::now());
                                    }
                                }
                            }
                            Ok(Some(Ok(Message::Close(_)))) | Ok(Some(Err(_))) | Ok(None) | Err(_) => break,
                            Ok(Some(Ok(_))) => {}
                        }
                    }
                }
                Ok(Err(e)) => eprintln!("Angel feed: {e}"),
                Err(_) => eprintln!("Angel feed connection timeout"),
            }
            if let Ok(mut s) = state.lock() { s.connected = false; s.price = None; s.quantity = None; }
            tokio::time::sleep(Duration::from_secs(backoff)).await;
            backoff = (backoff * 2).min(60);
        }
    }
    pub fn run() -> Result<(), Box<dyn std::error::Error>> {
        let state = Arc::new(Mutex::new(MarketState::default()));
        let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build()?;
        let task = runtime.spawn(stream(state.clone()));
        let result = eframe::run_native("Osiris Eye · Angel", eframe::NativeOptions::default(),
            Box::new(move |_cc| Box::new(AngelApp { state: state.clone() })));
        task.abort();
        result.map_err(|e| e.into())
    }
    #[cfg(test)]
    mod tests {
        use super::*;
        #[test]
        fn validates_trades() {
            assert_eq!(parse_trade(r#"{"e":"trade","s":"BTCUSDT","p":"90000","q":"0.1"}"#), Some((90000.0, 0.1)));
            assert!(parse_trade(r#"{"e":"trade","s":"BTCUSDT","p":"NaN","q":"0.1"}"#).is_none());
        }
    }
}
#[cfg(feature = "angel-desktop")]
fn main() -> Result<(), Box<dyn std::error::Error>> { desktop::run() }
#[cfg(not(feature = "angel-desktop"))]
fn main() { eprintln!("Enable desktop UI: cargo run --features angel-desktop --bin angel_desktop"); }
