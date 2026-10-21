# Anonymity and export handling

The review ZIP contains no `.git` directory, commit history, SSH configuration, personal credentials, Python bytecode/cache, or local virtual environment. Personal source-path defaults were replaced in this distribution copy; original working files were preserved. Review commands operate on the distributed source root.

Current source files and nested historical archive/checkpoint members were checked for known personal identifiers, credential patterns and private source-path markers. The outer review ZIP uses uniform timestamps and permissions and empty comments/extra fields. Historical v8 bytes are unchanged; its existing normalized metadata and members are inspected separately.

Git hosting is a transport, not an anonymous reviewer identity. Its repository owner and profile remain visible. Use the downloadable ZIP as a submission attachment; do not use the personal repository URL as an anonymous review link. The separately pushed branch starts with an anonymous root commit, but that does not anonymize the hosting account.

Original third-party attribution is retained; dependency authors are not treated as this submission's authors. The export adds no new license and preserves negative/unknown experimental records. A marker scan cannot guarantee immunity to all content-based reidentification.
