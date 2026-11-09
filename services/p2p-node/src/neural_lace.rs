//! Market sonification via local audio output (opt-in feature).
//! This is a monitoring aid, not a trading or medical neural interface.
#[cfg(feature = "audio")]
mod audio {
    use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
    use futures::StreamExt;
    use serde_json::Value;
    use std::{error::Error, f32::consts::TAU, sync::{Arc, Mutex}, time::Duration};
    use tokio_tungstenite::{connect_async, tungstenite::Message};

    #[derive(Default, Clone, Copy)]
    struct Pulse { price: f32, qty: f32 }
    fn trade(text: &str) -> Option<Pulse> {
        let json: Value = serde_json::from_str(text).ok()?;
        if json.get("e")?.as_str()? != "trade" { return None; }
        let price = json.get("p")?.as_str()?.parse::<f32>().ok()?;
        let qty = json.get("q")?.as_str()?.parse::<f32>().ok()?;
        (price.is_finite() && price > 0.0 && qty.is_finite() && qty >= 0.0)
            .then_some(Pulse { price, qty })
    }
    fn sample(phase: &mut f32, pulse: Pulse, rate: f32) -> f32 {
        let freq = (200.0 + (pulse.price / 100_000.0).clamp(0.0, 2.0) * 400.0).clamp(200.0, 1000.0);
        *phase = (*phase + TAU * freq / rate).rem_euclid(TAU);
        (*phase).sin() * (pulse.qty / 10.0).clamp(0.0, 0.03)
    }
    fn start_audio(pulse: Arc<Mutex<Pulse>>) -> Result<cpal::Stream, Box<dyn Error>> {
        let device = cpal::default_host().default_output_device().ok_or("no audio output device")?;
        let supported = device.default_output_config()?;
        let channels = supported.channels() as usize;
        let rate = supported.sample_rate().0 as f32;
        let mut phase = 0.0_f32;
        let error = |e| eprintln!("audio output error: {e}");
        let stream = match supported.sample_format() {
            cpal::SampleFormat::F32 => device.build_output_stream(&supported.config(), move |output: &mut [f32], _| {
                let p = pulse.lock().map(|guard| *guard).unwrap_or_default();
                for frame in output.chunks_mut(channels) {
                    let value = sample(&mut phase, p, rate);
                    for out in frame { *out = value; }
                }
            }, error, None)?,
            other => return Err(format!("unsupported audio sample format {other:?}; F32 required").into()),
        };
        stream.play()?;
        Ok(stream)
    }
    pub async fn run() -> Result<(), Box<dyn Error>> {
        let pulse = Arc::new(Mutex::new(Pulse::default()));
        let _audio_stream = start_audio(pulse.clone())?;
        let mut delay = 2_u64;
        println!("Neural Lace: low-volume market sonification; Ctrl+C to stop");
        loop {
            match tokio::time::timeout(Duration::from_secs(15),
                connect_async("wss://stream.binance.com:9443/ws/btcusdt@trade")).await {
                Ok(Ok((socket, _))) => {
                    delay = 2;
                    let (_, mut reader) = socket.split();
                    loop {
                        tokio::select! {
                            _ = tokio::signal::ctrl_c() => return Ok(()),
                            msg = tokio::time::timeout(Duration::from_secs(30), reader.next()) => {
                                match msg {
                                    Ok(Some(Ok(Message::Text(raw)))) => {
                                        if let Some(value) = trade(&raw) {
                                            if let Ok(mut guard) = pulse.lock() { *guard = value; }
                                        }
                                    }
                                    Ok(Some(Ok(Message::Close(_)))) | Ok(None) | Ok(Some(Err(_))) | Err(_) => break,
                                    Ok(Some(Ok(_))) => {}
                                }
                            }
                        }
                    }
                }
                Ok(Err(e)) => eprintln!("market feed error: {e}"),
                Err(_) => eprintln!("market feed connection timed out"),
            }
            if let Ok(mut guard) = pulse.lock() { *guard = Pulse::default(); }
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
        fn trade_validation() {
            assert!(trade(r#"{"e":"trade","p":"95000","q":"0.5"}"#).is_some());
            assert!(trade(r#"{"e":"trade","p":"NaN","q":"0.5"}"#).is_none());
        }
        #[test]
        fn oscillator_is_nonzero_and_bounded() {
            let mut phase = 0.0;
            let p = Pulse { price: 95_000.0, qty: 5.0 };
            let samples: Vec<_> = (0..200).map(|_| sample(&mut phase, p, 48_000.0)).collect();
            assert!(samples.iter().any(|s| s.abs() > 0.001));
            assert!(samples.iter().all(|s| s.abs() <= 0.03));
        }
    }
}
#[cfg(feature = "audio")]
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> { audio::run().await }
#[cfg(not(feature = "audio"))]
fn main() { eprintln!("Enable the audio feature: cargo run --features audio --bin neural_lace"); }
