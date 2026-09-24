# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.2.x (latest) | ✅ |
| older | ❌ |

Only the latest release receives security fixes.

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

- **Email:** cristobal.almendra02@gmail.com
- **Subject prefix:** `[econchile-security]`

You can expect:

1. An acknowledgement within **72 hours**.
2. A fix or workaround, and a patch release, as soon as practical
   (typically within a week for confirmed issues).
3. Credit in the release notes and CHANGELOG, if you want it.

## Scope

This project wraps the public Banco Central de Chile SIE REST API. It
stores no credentials of its own — the `BCCH_TOKEN` is read from the
environment or passed explicitly, and never written to disk by the
library. If you believe a token or secret was exposed (e.g. committed to
the repository), rotate it immediately and report it.

## Out of scope

- Issues in the BCCh SIE API itself (report them to the Banco Central de
  Chile).
- Misuse of the library by applications that embed secrets in code.
