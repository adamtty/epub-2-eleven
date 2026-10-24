# Security policy

## Reporting a vulnerability

Please report security issues **privately** through GitHub:
[**Report a vulnerability**](https://github.com/adamtty/epub-2-eleven/security/advisories/new).
Don't open a public issue for them.

Include what you found, how to reproduce it, and what an attacker could do with it. You'll get a
reply within a week. Once it's fixed, you'll be credited in the advisory unless you'd rather not be.

## Supported versions

Only the latest commit on `main` is supported; there are no release branches.

## Scope

In scope: the cleaner (`src/epubclean/`), the local server and its page (`server.py`, `web/`), and
`scripts/demo_book.py`. Especially interesting: an EPUB that can run script in the preview page,
read or write files outside what the user chose, make the server unresponsive or exhaust memory
beyond the documented caps, or get past the XML hardening; and anything that lets a web page or
another site drive the local server. See [Security](README.md#security) for the protections already
in place.

Out of scope: vulnerabilities in lxml/libxml2 themselves (report those upstream), ElevenLabs, and
attacks that need someone who can already run code as your user.
