# Security Policy

Please report security vulnerabilities **privately**, not in a public issue:
use GitHub's **Report a vulnerability** button on this repository's
[Security tab](https://github.com/sholtomaud/folding-grasshoppers/security/advisories/new).

Include what you found, how to reproduce it, and the app or engine version
(**Fold Theory > About**, or `curl http://127.0.0.1:5001/version`).

In scope: the Fold Theory app, its bundled Python engine and runtime, the
build/sign/notarize scripts, and the CI workflows. The engine is designed to
listen on `127.0.0.1` only; anything that makes it reachable from another
machine, or lets another local user stop or drive it without the shutdown
token, counts as a vulnerability.
