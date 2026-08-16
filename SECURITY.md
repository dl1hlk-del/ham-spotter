# Security Policy

## Sensitive files

Do not publish or attach any of these when reporting a problem:

- `.env`
- Telegram bot token or chat ID
- backup archives
- local database files
- private network details that are not required to reproduce the problem

The `.gitignore` excludes the standard runtime paths, but contributors should always inspect the staged changes before a commit.

## Reporting a security issue

Please do **not** open a public issue containing an exploitable vulnerability or credential. Until a dedicated private security contact is configured for the repository, contact the repository owner privately through the contact method listed on their GitHub profile.

## Scope

HAM Spotter is hobby software intended for a trusted local network. If the dashboard is exposed to the public Internet, use an appropriate reverse proxy, TLS and access controls. The project does not assume the dashboard itself is an Internet-facing security boundary.
