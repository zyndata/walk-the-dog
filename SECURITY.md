# Security policy

## Supported versions

Only the latest released version is supported. Report against it whenever possible.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting:
**Security → Advisories → Report a vulnerability** in this repository.

Please do not open a public issue for a security problem.

Expect an initial response within 14 days.

## Scope

This integration talks to public weather APIs and requires no credentials of its own. The most
likely security-relevant problems are therefore:

- leaking the user's coordinates or Home Assistant details into logs, events or outbound requests;
- unsafe handling of untrusted responses from a weather source (decompression, image decoding,
  unbounded memory use);
- notification content escaping into a context that treats it as markup or a command.

Bugs in Home Assistant itself belong to
[the Home Assistant security policy](https://github.com/home-assistant/core/security/policy).
