# Security policy

Please do not file a public issue containing credentials, cookies, tokens,
endpoint URLs, local-network details, site identifiers, serial numbers,
diagnostic downloads, or raw provider responses.

Report vulnerabilities through GitHub's private vulnerability reporting feature.

The local bridge is read-only. Portal mode provides monitoring and two explicit
manual EV controls. Reports demonstrating credential leakage,
credential forwarding outside the exact SolarEdge login/monitoring hosts,
acceptance of an untrusted login form or redirect, unbounded response processing,
or propagation of non-allowlisted provider data are security-sensitive.

The direct portal mode depends on an undocumented private interface. A provider
layout or authentication change is expected to fail closed. It must not be
worked around by broadening hosts, accepting arbitrary forms/redirects, logging
responses, or weakening the aggregate and EV output allowlists. EV discovery uses
the existing private session and a fixed HTTPS GET endpoint. Only an opaque
site-scoped device key and bounded status, mode and power fields are retained.
The stable device key is hashed, not encrypted; it is not a promise of anonymity.
Diagnostics include EV health and device counts, never provider identifiers or
raw device responses.

EV buttons use only the fixed HTTPS activationState endpoint for the configured
site. A fresh device list must uniquely resolve the opaque charger key before a
validated numeric reporter ID enters the URL. The reporter ID is transient;
no raw control identifier is added to entity attributes or diagnostics.

An anti-CSRF cookie is required. Redirects, failed HTTP status, missing validation
and application rejection fail closed. An ambiguous PUT is never retried
automatically. HTTP/application acceptance does not prove physical charging;
provider-reported mode/status/power remain authoritative. Manual requests can
persist across charging completion or unplugging until explicitly cancelled.
