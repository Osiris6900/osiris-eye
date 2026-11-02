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
    let projected_position = position + notional;
    if !notional.is_finite() || !projected_position.is_finite() {
        return Err(RejectReason::NonFiniteInput);
    }
    if notional > limits.max_order_notional {
        return Err(RejectReason::OrderLimit);
    }
    if projected_position > limits.max_position_notional {
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

    fn reason_code(result: Result<f64, RejectReason>) -> &'static str {
        match result {
            Ok(_) => "approved",
            Err(RejectReason::NonFiniteInput) => "non_finite_input",
            Err(RejectReason::InvalidOrder) => "invalid_order",
            Err(RejectReason::OrderLimit) => "order_limit",
            Err(RejectReason::PositionLimit) => "position_limit",
            Err(RejectReason::DailyLossLimit) => "daily_loss_limit",
        }
    }

    #[test]
    fn shared_python_rust_conformance_vectors() {
        let vectors = include_str!("../../../tests/risk_conformance.csv");
        let mut cases = 0;
        for line in vectors.lines().filter(|line| !line.is_empty() && !line.starts_with('#')) {
            let fields: Vec<&str> = line.split(',').collect();
            assert_eq!(fields.len(), 8, "bad conformance vector: {line}");
            let n: Vec<f64> = fields[..7]
                .iter()
                .map(|value| value.parse().expect("numeric conformance field"))
                .collect();
            let limits = Limits {
                max_order_notional: n[4],
                max_position_notional: n[5],
                max_daily_loss: n[6],
            };
            let result = check_order(n[0], n[1], n[2], n[3], limits);
            assert_eq!(reason_code(result), fields[7], "vector mismatch: {line}");
            cases += 1;
        }
        assert!(cases >= 10, "conformance fixture unexpectedly incomplete");
    }

    #[test]
    fn rejects_non_finite_projected_position() {
        let limits = Limits {
            max_order_notional: 1.7e308,
            max_position_notional: 1.7e308,
            max_daily_loss: 100.0,
        };
        assert_eq!(
            check_order(1.0e308, 1.0, 1.0e308, 0.0, limits),
            Err(RejectReason::NonFiniteInput)
        );
    }
}
