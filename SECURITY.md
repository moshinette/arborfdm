# Security policy

## Supported versions

Only the latest release receives fixes. ArborFDM is a hobby project maintained in spare time: there is no guaranteed response time.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub private vulnerability reporting: on the repository page, open the **Security** tab, then **Report a vulnerability**.

Useful information: version, operating system, how to reproduce (for example a crafted STL / OBJ / 3MF / `.arbor` file that crashes the app or writes outside the chosen folder), and the impact you expect.

## Scope

In scope:

- parsing of model files (STL, OBJ, 3MF) and `.arbor` projects (zip archives): crashes, excessive memory use, path traversal, code execution;
- the release packages published on this repository (wrong checksums, tampered files).

Out of scope: vulnerabilities in dependencies that are already fixed upstream (please tell us so we update the pinned versions in `packaging/requirements-build.txt`), and antivirus false positives on the unsigned PyInstaller executables (see `docs/PACKAGING.md`).

## Verifying a download

Each release lists the SHA-256 of the zip in `SHA256SUMS.txt`. On Windows:

```
certutil -hashfile ArborFDM-<version>-windows-x64.zip SHA256
```

Only download ArborFDM from this repository's Releases page.
