//! Deterministic risk gate. Does not submit or route orders.
#[derive(Debug, Clone, Copy)]
pub struct Limits {
    pub max_order_notional: f64,
    pub max_position_notional: f64,
    pub max_daily_loss: f64,
}

#[derive(Debug, PartialEq, Eq)]
pub enum RejectReason {
    NonFiniteInput,
    InvalidOrder,
    OrderLimit,
    PositionLimit,
    DailyLossLimit,
}

pub fn check_order(
    qty: f64,
    price: f64,
    position: f64,
    daily_pnl: f64,
    limits: Limits,
) -> Result<f64, RejectReason> {
    let values = [
        qty,
        price,
        position,
        daily_pnl,
        limits.max_order_notional,
        limits.max_position_notional,
        limits.max_daily_loss,
    ];
    if values.iter().any(|value| !value.is_finite()) {
        return Err(RejectReason::NonFiniteInput);
    }
    if qty <= 0.0
        || price <= 0.0
        || position < 0.0
        || limits.max_order_notional <= 0.0
        || limits.max_position_notional <= 0.0
        || limits.max_daily_loss < 0.0
    {
        return Err(RejectReason::InvalidOrder);
    }

    let notional = qty * price;
    if !notional.is_finite() {
        return Err(RejectReason::NonFiniteInput);
    }
    if notional > limits.max_order_notional {
        return Err(RejectReason::OrderLimit);
    }
    if position + notional > limits.max_position_notional {
        return Err(RejectReason::PositionLimit);
    }
    if daily_pnl <= -limits.max_daily_loss {
        return Err(RejectReason::DailyLossLimit);
    }

    Ok(notional)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn limits() -> Limits {
        Limits {
            max_order_notional: 1_000.0,
            max_position_notional: 2_000.0,
            max_daily_loss: 100.0,
        }
    }

    #[test]
    fn allow_small_simulated_order() {
        assert_eq!(check_order(2.0, 100.0, 0.0, 0.0, limits()), Ok(200.0));
    }

    #[test]
    fn reject_large_order() {
        assert_eq!(
            check_order(20.0, 100.0, 0.0, 0.0, limits()),
            Err(RejectReason::OrderLimit)
        );
    }

    #[test]
    fn reject_nan_input() {
        assert_eq!(
            check_order(f64::NAN, 1.0, 0.0, 0.0, limits()),
            Err(RejectReason::NonFiniteInput)
        );
    }

    #[test]
    fn reject_overflowed_notional() {
        assert_eq!(
            check_order(f64::MAX, 2.0, 0.0, 0.0, limits()),
            Err(RejectReason::NonFiniteInput)
        );
    }
}
