# Historical manuscript replication material

`e2r_revision_v8/` is the fully expanded historical release. All 7,321 regular files preserve their source bytes, including the internal manifest, observations, checkpoints, and qualified audit records. No extraction is required.

From the repository root, verify the historical files:

```bash
python paper-replication/e2r_revision_v8/scripts/e2r/verify_anonymous_artifact.py \
  --artifact-root paper-replication/e2r_revision_v8
```

Then read `e2r_revision_v8/README.md` and its assurance and evidence limitations before running historical analyses. Current-source tests and the newer context example are described in the top-level README and have separate evidence scope.

`SOURCE_PROVENANCE.json` records the source archive digest and the byte-preserving expansion. The original `e2r_revision_v8.release.json` and `e2r_revision_v8.audit/` records are retained as historical attestations. Archive filenames and digests in those records identify the original release; no ZIP or tar download is supplied in this file tree. The nested historical records themselves have not been rewritten to describe the new delivery layout.
