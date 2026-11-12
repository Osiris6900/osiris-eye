//! CLI over immutable local binary capture files. One symbol per invocation.
use memmap2::MmapOptions;
use osiris_native_feed::{channel, TickView};
use std::fs::File;
use std::sync::{Arc, atomic::{AtomicBool, Ordering}};

fn run() -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    let mut workers = 1usize;
    let mut prefetch = false;
    let mut files = Vec::new();
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--workers" => { workers = args.next().ok_or("missing workers")?.parse()?; }
            "--prefetch" => { prefetch = true; }
            "--file" => { files.push(args.next().ok_or("missing file")?); }
            _ => return Err("unsupported argument".into()),
        }
    }
    if !(1..=16).contains(&workers) || files.is_empty() || files.len() > 8 {
        return Err("1-16 workers and 1-8 immutable files required".into());
    }
    let mut maps = Vec::new();
    let mut total_bytes = 0u64;
    for path in files {
        let file = File::open(path)?;
        let length = file.metadata()?.len();
        total_bytes = total_bytes.checked_add(length).ok_or("size overflow")?;
        if length == 0 || length % 28 != 0 || total_bytes > 67_108_864 {
            return Err("aligned capture files totaling <=64MiB required".into());
        }
        // SAFETY: the operator supplies immutable capture files. The CLI never
        // writes/truncates them; concurrent external mutation is unsupported.
        maps.push(unsafe { MmapOptions::new().map(&file)? });
    }
    let maps = Arc::new(maps);
    let done = Arc::new(AtomicBool::new(false));
    let mut producers = Vec::new();
    let mut handles = Vec::new();
    for _ in 0..workers {
        let (tx, mut rx) = channel::<(usize, usize)>(1024)?;
        producers.push(tx);
        let (maps, done) = (maps.clone(), done.clone());
        handles.push(std::thread::spawn(move || {
            let (mut count, mut notional) = (0u64, 0.0f64);
            loop {
                let finished = done.load(Ordering::Acquire);
                if let Some((file, offset)) = rx.pop() {
                    let tick = TickView::parse(&maps[file][offset..offset + 28]).unwrap();
                    if prefetch { tick.prefetch(); }
                    count += 1;
                    notional += tick.price() * tick.size();
                } else if finished { break; }
                else { std::thread::yield_now(); }
            }
            (count, notional)
        }));
    }
    let (mut accepted, mut rejected, mut duplicates) = (0u64, 0u64, 0u64);
    let mut last = None;
    for (file, map) in maps.iter().enumerate() {
        for offset in (0..map.len()).step_by(28) {
            let Ok(tick) = TickView::parse(&map[offset..offset + 28]) else { rejected += 1; continue; };
            let sequence = tick.sequence();
            if let Some(previous) = last {
                let distance = sequence.wrapping_sub(previous);
                if distance == 0 || distance >= 0x8000_0000 { duplicates += 1; continue; }
            }
            last = Some(sequence);
            let mut item = (file, offset);
            let worker = (accepted as usize) % workers;
            while let Err(value) = producers[worker].push(item) { item = value; std::thread::yield_now(); }
            accepted += 1;
        }
    }
    done.store(true, Ordering::Release);
    let mut processed = 0u64;
    let mut notional = 0.0;
    for handle in handles {
        let (count, value) = handle.join().map_err(|_| "native worker failed")?;
        processed += count; notional += value;
    }
    if processed != accepted { return Err("native accounting mismatch".into()); }
    Ok(serde_json::json!({"processed": processed, "invalid": rejected,
        "duplicates_or_out_of_order": duplicates, "notional_sum": notional, "workers": workers,
        "memory_mapping": true, "borrowed_tick_views": true, "spsc_queues": true,
        "prefetch_requested": prefetch, "prefetch_supported": cfg!(target_arch="x86_64"),
        "capture_immutability_operator_required": true, "live_execution": false}))
}

fn main() {
    match run() {
        Ok(value) => println!("{value}"),
        Err(error) => { eprintln!("native-feed: {error}"); std::process::exit(1); }
    }
}
