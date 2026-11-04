//! UTC observation timestamp validation and optional live-data freshness checks.
//!
//! Accepted wire form is `YYYY-MM-DDTHH:MM:SS[.1-9 digits]Z`, from 1970 through
//! 9999. UTC offsets, leap seconds, and pre-epoch timestamps are rejected rather
//! than normalized. Freshness is opt-in so historical research observations can
//! retain their original event time. This validates time, not provider truth.

pub const MAX_OBSERVATION_AGE_SECONDS: u64 = 86_400;
pub const MAX_FUTURE_SKEW_SECONDS: u64 = 30;

/// Parse an optional operator setting once at startup. Empty/zero/oversized or
/// non-decimal settings fail closed; an absent setting preserves research mode.
pub fn parse_max_age(raw: Option<&str>) -> Result<Option<u64>, &'static str> {
    let Some(raw) = raw else { return Ok(None) };
    if raw.is_empty() || raw.len() > 5 || !raw.bytes().all(|b| b.is_ascii_digit()) {
        return Err("invalid_observation_max_age_seconds");
    }
    let value = raw.parse::<u64>()
        .map_err(|_| "invalid_observation_max_age_seconds")?;
    if !(1..=MAX_OBSERVATION_AGE_SECONDS).contains(&value) {
        return Err("invalid_observation_max_age_seconds");
    }
    Ok(Some(value))
}

/// Validate syntax always; when `max_age` is set also reject data older than
/// that many seconds and event times more than 30 seconds ahead of `now`.
/// `now` is an injected Unix timestamp in seconds, making boundary checks
/// deterministic and independent of the node's timestamp parser.
pub fn validate_timestamp(
    timestamp: &str,
    now: u64,
    max_age: Option<u64>,
) -> Result<(), &'static str> {
    let (observed_at, fractional_nonzero) = parse_utc(timestamp)?;
    if let Some(max_age) = max_age {
        if !(1..=MAX_OBSERVATION_AGE_SECONDS).contains(&max_age) {
            return Err("invalid_observation_max_age_seconds");
        }
        let ahead = observed_at.saturating_sub(now);
        if ahead > MAX_FUTURE_SKEW_SECONDS
            || (ahead == MAX_FUTURE_SKEW_SECONDS && fractional_nonzero)
        {
            return Err("observation_from_future");
        }
        if now.saturating_sub(observed_at) > max_age {
            return Err("stale_observation");
        }
    }
    Ok(())
}

fn decimal(bytes: &[u8]) -> Result<u64, &'static str> {
    bytes.iter().try_fold(0, |value, digit| {
        if !digit.is_ascii_digit() { return Err("invalid_observed_at"); }
        Ok(value * 10 + u64::from(digit - b'0'))
    })
}

fn leap_year(year: u64) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
}

fn parse_utc(timestamp: &str) -> Result<(u64, bool), &'static str> {
    let b = timestamp.as_bytes();
    if !(20..=30).contains(&b.len()) || !b.is_ascii()
        || b[4] != b'-' || b[7] != b'-' || b[10] != b'T'
        || b[13] != b':' || b[16] != b':' || b[b.len() - 1] != b'Z'
    {
        return Err("invalid_observed_at");
    }
    let fractional_nonzero = if b.len() == 20 {
        false
    } else {
        if b.len() < 22 || b[19] != b'.' {
            return Err("invalid_observed_at");
        }
        let fraction = &b[20..b.len() - 1];
        decimal(fraction)?;
        fraction.iter().any(|digit| *digit != b'0')
    };
    let year = decimal(&b[0..4])?;
    let month = decimal(&b[5..7])?;
    let day = decimal(&b[8..10])?;
    let hour = decimal(&b[11..13])?;
    let minute = decimal(&b[14..16])?;
    let second = decimal(&b[17..19])?;
    if year < 1970 || !(1..=12).contains(&month) || hour > 23 || minute > 59 || second > 59 {
        return Err("invalid_observed_at");
    }
    let month_days = [31, if leap_year(year) { 29 } else { 28 }, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    if day == 0 || day > month_days[(month - 1) as usize] {
        return Err("invalid_observed_at");
    }
    // The four-digit year and validated components bound all arithmetic well
    // below u64::MAX. Count Gregorian leap days before this year and subtract
    // those before 1970 (477).
    let previous = year - 1;
    let days = (year - 1970) * 365
        + previous / 4 - previous / 100 + previous / 400 - 477
        + month_days[..(month - 1) as usize].iter().sum::<u64>() + day - 1;
    Ok((days * 86_400 + hour * 3600 + minute * 60 + second, fractional_nonzero))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn converts_epoch_and_known_leap_date() {
        assert_eq!(parse_utc("1970-01-01T00:00:00Z"), Ok((0, false)));
        assert_eq!(parse_utc("2000-02-29T12:34:56Z"), Ok((951_827_696, false)));
        assert_eq!(parse_utc("9999-12-31T23:59:59Z"), Ok((253_402_300_799, false)));
    }

    #[test]
    fn accepts_one_through_nine_fraction_digits() {
        assert_eq!(parse_utc("1970-01-01T00:00:00.0Z"), Ok((0, false)));
        assert_eq!(parse_utc("1970-01-01T00:00:00.000000001Z"), Ok((0, true)));
        assert!(parse_utc("1970-01-01T00:00:00.Z").is_err());
        assert!(parse_utc("1970-01-01T00:00:00.0000000000Z").is_err());
    }

    #[test]
    fn rejects_impossible_dates_and_clock_fields() {
        for timestamp in [
            "1969-12-31T23:59:59Z", "1900-02-29T00:00:00Z", "2100-02-29T00:00:00Z",
            "2023-02-29T00:00:00Z", "2024-04-31T00:00:00Z", "2024-00-01T00:00:00Z",
            "2024-13-01T00:00:00Z", "2024-01-00T00:00:00Z", "2024-01-01T24:00:00Z",
            "2024-01-01T00:60:00Z", "2024-01-01T00:00:60Z",
        ] { assert!(parse_utc(timestamp).is_err(), "accepted {timestamp}"); }
        assert!(parse_utc("2400-02-29T00:00:00Z").is_ok());
    }

    #[test]
    fn rejects_offsets_whitespace_non_ascii_and_alternate_syntax() {
        for timestamp in [
            "", "2024-01-01", "2024-01-01t00:00:00Z", "2024-01-01 00:00:00Z",
            "2024-01-01T00:00:00z", "2024-01-01T00:00:00+00:00",
            "2024-01-01T00:00:00Z ", "2024-01-01T00:00:0éZ",
            "2024-01-01T00:00:00.1xZ", "2024-01-01T00:00:00\0Z",
        ] { assert!(parse_utc(timestamp).is_err(), "accepted {timestamp:?}"); }
    }

    #[test]
    fn freshness_age_is_inclusive_and_fraction_does_not_make_new_data_stale() {
        assert!(validate_timestamp("1970-01-01T00:00:00Z", 300, Some(300)).is_ok());
        assert!(validate_timestamp("1970-01-01T00:00:00.1Z", 300, Some(300)).is_ok());
        assert_eq!(validate_timestamp("1970-01-01T00:00:00Z", 301, Some(300)), Err("stale_observation"));
    }

    #[test]
    fn future_skew_is_inclusive_but_fraction_beyond_limit_is_rejected() {
        assert!(validate_timestamp("1970-01-01T00:00:30Z", 0, Some(300)).is_ok());
        assert_eq!(validate_timestamp("1970-01-01T00:00:30.1Z", 0, Some(300)), Err("observation_from_future"));
        assert_eq!(validate_timestamp("1970-01-01T00:00:31Z", 0, Some(300)), Err("observation_from_future"));
    }

    #[test]
    fn historical_research_mode_validates_format_without_time_window() {
        assert!(validate_timestamp("1970-01-01T00:00:00Z", u64::MAX, None).is_ok());
        assert!(validate_timestamp("9999-12-31T23:59:59Z", 0, None).is_ok());
        assert!(validate_timestamp("not-a-date", 0, None).is_err());
        assert_eq!(validate_timestamp("1970-01-01T00:00:00Z", u64::MAX, Some(1)), Err("stale_observation"));
    }

    #[test]
    fn operator_age_configuration_is_bounded_and_fail_closed() {
        assert_eq!(parse_max_age(None), Ok(None));
        assert_eq!(parse_max_age(Some("1")), Ok(Some(1)));
        assert_eq!(parse_max_age(Some("86400")), Ok(Some(86_400)));
        for raw in ["", "0", "86401", "-1", "+1", "1.5", " 300", "NaN", "9999999999999999999"] {
            assert!(parse_max_age(Some(raw)).is_err(), "accepted {raw}");
        }
        assert_eq!(validate_timestamp("1970-01-01T00:00:00Z", 0, Some(0)), Err("invalid_observation_max_age_seconds"));
        assert_eq!(validate_timestamp("1970-01-01T00:00:00Z", 0, Some(86_401)), Err("invalid_observation_max_age_seconds"));
    }
}
