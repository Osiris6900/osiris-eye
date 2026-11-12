//! Bounded SPSC queues and borrowed binary tick views. No trading/network access.
use std::cell::UnsafeCell;
use std::mem::MaybeUninit;
use std::sync::{atomic::{AtomicUsize, Ordering}, Arc};

struct Ring<T> {
    slots: Box<[UnsafeCell<MaybeUninit<T>>]>,
    head: AtomicUsize,
    tail: AtomicUsize,
    mask: usize,
}
// SAFETY: split() creates exactly one producer and consumer, neither Clone.
// Each slot is owned by the producer until head's release, then by the consumer
// until tail's release. Acquire loads prevent reuse while a reader owns a slot.
unsafe impl<T: Send> Send for Ring<T> {}
unsafe impl<T: Send> Sync for Ring<T> {}

impl<T> Drop for Ring<T> {
    fn drop(&mut self) {
        let mut tail = *self.tail.get_mut();
        let head = *self.head.get_mut();
        while tail != head {
            // SAFETY: Arc has no remaining endpoints. Published unread slots
            // are initialized, and no other thread can access them now.
            unsafe { (*self.slots[tail & self.mask].get()).assume_init_drop(); }
            tail = tail.wrapping_add(1);
        }
    }
}

pub struct Producer<T>(Arc<Ring<T>>);
pub struct Consumer<T>(Arc<Ring<T>>);

pub fn channel<T: Send>(capacity: usize) -> Result<(Producer<T>, Consumer<T>), &'static str> {
    if capacity < 2 || capacity > 1_048_576 || !capacity.is_power_of_two() {
        return Err("bounded power-of-two capacity required");
    }
    let ring = Arc::new(Ring {
        slots: (0..capacity).map(|_| UnsafeCell::new(MaybeUninit::uninit())).collect::<Vec<_>>().into_boxed_slice(),
        head: AtomicUsize::new(0), tail: AtomicUsize::new(0), mask: capacity - 1,
    });
    Ok((Producer(ring.clone()), Consumer(ring)))
}

impl<T> Producer<T> {
    pub fn push(&mut self, value: T) -> Result<(), T> {
        let head = self.0.head.load(Ordering::Relaxed);
        let tail = self.0.tail.load(Ordering::Acquire);
        if head.wrapping_sub(tail) >= self.0.slots.len() { return Err(value); }
        // SAFETY: the acquire tail check gives exclusive ownership of this slot.
        unsafe { (*self.0.slots[head & self.0.mask].get()).write(value); }
        self.0.head.store(head.wrapping_add(1), Ordering::Release);
        Ok(())
    }
}

impl<T> Consumer<T> {
    pub fn pop(&mut self) -> Option<T> {
        let tail = self.0.tail.load(Ordering::Relaxed);
        let head = self.0.head.load(Ordering::Acquire);
        if tail == head { return None; }
        // SAFETY: the acquire head check proves initialized ownership.
        let value = unsafe { (*self.0.slots[tail & self.0.mask].get()).assume_init_read() };
        self.0.tail.store(tail.wrapping_add(1), Ordering::Release);
        Some(value)
    }
}

#[derive(Clone, Copy)]
pub struct TickView<'a>(&'a [u8]);

impl<'a> TickView<'a> {
    pub fn parse(bytes: &'a [u8]) -> Result<Self, &'static str> {
        if bytes.len() != 28 { return Err("invalid tick size"); }
        let view = Self(bytes);
        if view.timestamp_ns() == 0 || !view.price().is_finite() || !view.size().is_finite()
            || view.price() <= 0.0 || view.price() > 1e15 || view.size() <= 0.0 || view.size() > 1e15 {
            return Err("invalid tick values");
        }
        Ok(view)
    }
    pub fn timestamp_ns(self) -> u64 { u64::from_le_bytes(self.0[0..8].try_into().unwrap()) }
    pub fn price(self) -> f64 { f64::from_le_bytes(self.0[8..16].try_into().unwrap()) }
    pub fn size(self) -> f64 { f64::from_le_bytes(self.0[16..24].try_into().unwrap()) }
    pub fn sequence(self) -> u32 { u32::from_le_bytes(self.0[24..28].try_into().unwrap()) }
    pub fn bytes(self) -> &'a [u8] { self.0 }
    pub fn prefetch(self) {
        #[cfg(target_arch = "x86_64")]
        // SAFETY: address points into the borrowed, validated 28-byte frame.
        unsafe { std::arch::x86_64::_mm_prefetch(self.0.as_ptr().cast(), std::arch::x86_64::_MM_HINT_T0); }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bounded_fifo_does_not_overwrite() {
        let (mut tx, mut rx) = channel(2).unwrap();
        tx.push(1).unwrap(); tx.push(2).unwrap();
        assert_eq!(tx.push(3), Err(3));
        assert_eq!(rx.pop(), Some(1)); tx.push(3).unwrap();
        assert_eq!(rx.pop(), Some(2)); assert_eq!(rx.pop(), Some(3));
        assert_eq!(rx.pop(), None);
    }
    #[test]
    fn two_threads_preserve_every_record() {
        let (mut tx, mut rx) = channel(64).unwrap();
        let producer = std::thread::spawn(move || {
            for mut item in 0..100_000 {
                while let Err(value) = tx.push(item) { item = value; std::thread::yield_now(); }
            }
        });
        for expected in 0..100_000 {
            let value = loop { if let Some(value) = rx.pop() { break value; } std::thread::yield_now(); };
            assert_eq!(value, expected);
        }
        producer.join().unwrap();
    }
    #[test]
    fn view_borrows_without_copy_and_rejects_nan() {
        let mut raw = [0u8; 28];
        raw[..8].copy_from_slice(&1u64.to_le_bytes());
        raw[8..16].copy_from_slice(&100.0f64.to_le_bytes());
        raw[16..24].copy_from_slice(&2.0f64.to_le_bytes());
        let view = TickView::parse(&raw).unwrap();
        assert_eq!(view.bytes().as_ptr(), raw.as_ptr());
        assert_eq!(view.price(), 100.0);
        raw[8..16].copy_from_slice(&f64::NAN.to_le_bytes());
        assert!(TickView::parse(&raw).is_err());
    }
}
