## ADDED Requirements

### Requirement: Read-only code and date-range query
The system SHALL provide a read-only CLI query on the standalone database by valid six-digit fund code and inclusive start/end calendar dates. It SHALL return only stored rows in ascending date order with ISO date strings, nullable unit and accumulated NAV fields, and present NAVs represented as decimal strings. It SHALL NOT imply every date between the returned first and last rows exists. An unknown code or interval with no stored rows SHALL return an empty result; invalid codes, invalid dates, or reversed bounds SHALL fail with a clear input error and SHALL NOT modify data.

#### Scenario: Inclusive bounds and nullable values
- **WHEN** stored NAV exists on the start date, on the end date, and outside the requested interval, with one in-range indicator missing
- **THEN** the query returns only the two in-range rows in ascending date order, preserving the missing indicator as NULL and non-NULL NAV as decimal strings

#### Scenario: No matching fund or dates
- **WHEN** a valid code is unknown or there are no stored rows between valid bounds
- **THEN** the query returns an empty result without fetching upstream or writing database rows

#### Scenario: Invalid interval
- **WHEN** the start date is not a real calendar date or is after the end date
- **THEN** the query rejects the input rather than changing stored NAV
