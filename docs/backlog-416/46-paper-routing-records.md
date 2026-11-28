# Package 46: paper execution records

The paper book had orders and fills, and no per-order execution record.
`GET /paper/execution/routes` builds one record for each accepted, partial,
filled, or cancelled order.

The order limit is the decision price. The average fill is the execution.
Shortfall is exact, in basis points, buy `(fill - limit) / limit` and sell
`(limit - fill) / limit`. An unfilled order has no shortfall. The venue is the
instrument master venue when the symbol is unambiguous. The route is
`paper_book`.

A caller price or shortfall on the query string is ignored. `POST` is not
defined. A corrupt audit or a reconciliation break returns `unavailable` and
no routes. `live_routing_enabled` stays false. This is not a broker router,
a smart-order router, or a measured arrival-price benchmark from a venue feed.
