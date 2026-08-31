# Security policy

Please do not file a public issue containing credentials, cookies, tokens,
endpoint URLs, local-network details, site identifiers, serial numbers,
diagnostic downloads, or raw provider responses.

Report vulnerabilities through GitHub's private vulnerability reporting feature.

The integration is read-only. Reports demonstrating credential leakage,
credential forwarding outside the exact SolarEdge login/monitoring hosts,
acceptance of an untrusted login form or redirect, unbounded response processing,
or propagation of non-allowlisted provider data are security-sensitive.

The direct portal mode depends on an undocumented private interface. A provider
layout or authentication change is expected to fail closed. It must not be
worked around by broadening hosts, accepting arbitrary forms/redirects, logging
responses, or weakening the aggregate output allowlist.

