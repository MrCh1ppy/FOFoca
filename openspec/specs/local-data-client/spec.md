# local-data-client Specification

## Purpose
Define authenticated read-only local access to stored NAV history with exact values, validated responses, bounded requests, and protected credentials.

## Requirements

### Requirement: Fetch stored NAV history from the authenticated API
The local `data` module SHALL fetch NAV history only from the configured FOFoca read-only HTTP API using its inclusive range endpoint and configured Basic credentials. It SHALL NOT connect to PostgreSQL, invoke the cloud ingestion application, fetch upstream provider data, or write data.

#### Scenario: Successful range query
- **WHEN** a caller requests a valid six-digit fund code and inclusive start/end dates with valid client configuration
- **THEN** the client sends an authenticated GET to the configured API and returns the response rows in ascending date order

#### Scenario: Empty stored range
- **WHEN** the API returns a successful response with no matching rows
- **THEN** the client returns an empty collection and does not synthesize dates or fetch another source

### Requirement: Preserve exact NAV and missing data semantics
The client SHALL preserve each row's date, `unit_nav`, and `accumulated_nav` independently. Present decimal values SHALL remain exact decimal strings or an equally exact decimal representation; JSON null SHALL remain absent and missing calendar dates SHALL not be synthesized.

#### Scenario: NAV fields are nullable and distinct
- **WHEN** the API returns rows with null `unit_nav`, null `accumulated_nav`, or both fields present
- **THEN** the returned record preserves each field independently without substituting one for the other

#### Scenario: Date gaps remain gaps
- **WHEN** the API response omits weekends, holidays, or other dates
- **THEN** the returned history contains only the supplied rows in ascending order

### Requirement: Validate client configuration and API response
The client SHALL require an explicit valid base URL, credentials, and bounded request timeout. It SHALL validate HTTP status, response code and requested range identity, row date bounds/order, and response field types before returning data. It SHALL return typed errors for configuration, transport, HTTP, and response contract failures.

#### Scenario: Invalid configuration or response
- **WHEN** configuration is invalid or the API response is unsuccessful, malformed, out of range, or inconsistent with the request
- **THEN** the client returns a typed error and no partial history

#### Scenario: Request timeout
- **WHEN** the configured API does not respond within the allowed timeout
- **THEN** the client returns a transport/timeout error without retrying indefinitely

### Requirement: Protect credentials
The client SHALL avoid logging credentials, Authorization headers, or secret-bearing URLs and SHALL NOT forward Basic credentials to an untrusted redirect origin.

#### Scenario: Redirect cannot disclose credentials
- **WHEN** the configured endpoint responds with a redirect to another origin
- **THEN** the client does not send configured Basic credentials to that origin and returns a safe error or handles only an explicitly trusted same-origin redirect

#### Scenario: Diagnostics are redacted
- **WHEN** configuration, transport, or HTTP errors are reported
- **THEN** diagnostic text contains no username, password, or Authorization value
