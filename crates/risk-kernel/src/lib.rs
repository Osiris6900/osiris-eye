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
    KillSwitchActive,
    StaleMarketData,
    ExposureBudget,
    RiskServiceUnavailable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OrderSide {
    Buy,
    Sell,
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


/// Operator and feed-health gates for paper execution. Fail closed before
/// evaluating the ordinary notional/position/daily-loss limits.
#[derive(Debug, Clone, Copy)]
pub struct ExecutionControls {
    pub kill_switch_active: bool,
    pub market_data_fresh: bool,
}

pub fn check_order_with_controls(
    qty: f64,
    price: f64,
    position: f64,
    daily_pnl: f64,
    limits: Limits,
    controls: ExecutionControls,
) -> Result<f64, RejectReason> {
    if controls.kill_switch_active {
        return Err(RejectReason::KillSwitchActive);
    }
    if !controls.market_data_fresh {
        return Err(RejectReason::StaleMarketData);
    }
    check_order(qty, price, position, daily_pnl, limits)
}

/// Paper-only gate. Pre-trade checks run before notional limits.
/// Does not submit or route orders.
pub fn check_execution(
    side: OrderSide,
    qty: f64,
    price: f64,
    position: f64,
    gross: f64,
    daily_pnl: f64,
    limits: Limits,
    max_gross_exposure: f64,
    controls: ExecutionControls,
    risk_service_available: bool,
) -> Result<f64, RejectReason> {
    if !risk_service_available {
        return Err(RejectReason::RiskServiceUnavailable);
    }
    if controls.kill_switch_active {
        return Err(RejectReason::KillSwitchActive);
    }
    if !controls.market_data_fresh {
        return Err(RejectReason::StaleMarketData);
    }
    let inputs = [qty, price, position, gross, daily_pnl];
    if inputs.iter().any(|value| !value.is_finite()) {
        return Err(RejectReason::NonFiniteInput);
    }
    let limit_values = [
        limits.max_order_notional,
        limits.max_position_notional,
        limits.max_daily_loss,
        max_gross_exposure,
    ];
    if limit_values.iter().any(|value| !value.is_finite())
        || qty <= 0.0
        || price <= 0.0
        || position < 0.0
        || gross < 0.0
        || limits.max_order_notional <= 0.0
        || limits.max_position_notional <= 0.0
        || limits.max_daily_loss < 0.0
        || max_gross_exposure <= 0.0
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
    match side {
        OrderSide::Buy => {
            let projected_position = position + notional;
            let projected_gross = gross + notional;
            if !projected_position.is_finite() || !projected_gross.is_finite() {
                return Err(RejectReason::NonFiniteInput);
            }
            if projected_position > limits.max_position_notional {
                return Err(RejectReason::PositionLimit);
            }
            if daily_pnl <= -limits.max_daily_loss {
                return Err(RejectReason::DailyLossLimit);
            }
            if projected_gross > max_gross_exposure {
                return Err(RejectReason::ExposureBudget);
            }
            Ok(notional)
        }
        OrderSide::Sell => {
            if notional > position || notional > gross {
                return Err(RejectReason::InvalidOrder);
            }
            Ok(notional)
        }
    }
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
            Err(RejectReason::KillSwitchActive) => "kill_switch_active",
            Err(RejectReason::StaleMarketData) => "stale_market_data",
            Err(RejectReason::ExposureBudget) => "exposure_budget",
            Err(RejectReason::RiskServiceUnavailable) => "risk_service_unavailable",
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
    fn kill_switch_blocks_even_valid_order() {
        let limits = Limits { max_order_notional: 1000.0, max_position_notional: 2000.0, max_daily_loss: 100.0 };
        let controls = ExecutionControls { kill_switch_active: true, market_data_fresh: true };
        assert_eq!(check_order_with_controls(1.0, 100.0, 0.0, 0.0, limits, controls), Err(RejectReason::KillSwitchActive));
    }

    #[test]
    fn stale_feed_blocks_even_valid_order() {
        let limits = Limits { max_order_notional: 1000.0, max_position_notional: 2000.0, max_daily_loss: 100.0 };
        let controls = ExecutionControls { kill_switch_active: false, market_data_fresh: false };
        assert_eq!(check_order_with_controls(1.0, 100.0, 0.0, 0.0, limits, controls), Err(RejectReason::StaleMarketData));
    }

    #[test]
    fn healthy_controls_preserve_existing_risk_limits() {
        let limits = Limits { max_order_notional: 1000.0, max_position_notional: 2000.0, max_daily_loss: 100.0 };
        let controls = ExecutionControls { kill_switch_active: false, market_data_fresh: true };
        assert_eq!(check_order_with_controls(1.0, 100.0, 0.0, 0.0, limits, controls), Ok(100.0));
        assert_eq!(check_order_with_controls(20.0, 100.0, 0.0, 0.0, limits, controls), Err(RejectReason::OrderLimit));
    }

    #[test]
    fn shared_execution_control_vectors() {
        let vectors = include_str!("../../../tests/risk_execution_conformance.csv");
        let mut cases = 0;
        for line in vectors.lines().filter(|line| !line.is_empty() && !line.starts_with('#')) {
            let fields: Vec<&str> = line.split(',').collect();
            assert_eq!(fields.len(), 14, "bad execution vector: {line}");
            let side = match fields[0] {
                "buy" => OrderSide::Buy,
                "sell" => OrderSide::Sell,
                other => panic!("bad side {other}"),
            };
            let n: Vec<f64> = fields[1..10]
                .iter()
                .map(|value| value.parse().expect("numeric execution field"))
                .collect();
            let limits = Limits {
                max_order_notional: n[5],
                max_position_notional: n[6],
                max_daily_loss: n[7],
            };
            let controls = ExecutionControls {
                kill_switch_active: fields[10] == "1",
                market_data_fresh: fields[11] == "1",
            };
            let result = check_execution(
                side, n[0], n[1], n[2], n[3], n[4], limits, n[8], controls, fields[12] == "1",
            );
            assert_eq!(reason_code(result), fields[13], "vector mismatch: {line}");
            cases += 1;
        }
        assert!(cases >= 15, "execution conformance fixture unexpectedly incomplete");
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
