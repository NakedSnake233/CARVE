# Evidence scope

## Manuscript-linked historical evidence

`paper-replication/e2r_revision_v8.tar.gz` is the unchanged archive cited in the supplied manuscript. Its SHA-256 is `76d5ce9f035a0b42a27fc8e6c82c58e0d663a65d06212b4c39016a4247c60c4b`. It includes the original snapshot, observations, models, analyses, and qualified audit records. Read its own scope limitations, including interrupted or invalid comparisons. Its source and numerical claims are not silently replaced by newer code.

## Additional current-code example

The context-repair data contains three distinct recorded cohorts of 24 positions each: training, validation, and test. Each cohort preserves 23 observed outcomes and one rejected/unknown outcome. The frozen model was fit only on training; validation did not select parameters; the test cohort was evaluated after freezing. Four online policies each have eight recorded positions. These were controlled runs on one native Station/MySQL slice under known fault mechanisms, not independent production incidents.

The public rows export measured labels and pre-action derived features from those recorded runs. Paths and attempt identifiers are anonymized. `labels_verified` records validation performed at the original collection/export stage; this compact bundle does not include the raw HTTP/Kubernetes evidence required to independently rederive those labels. Thus the included replay establishes numerical reproducibility from the exported feature tables, not independent attestation of all underlying physical events.

The model's numerical coefficients are unchanged; anonymized provenance paths alter its file digest. The historical private provenance digests in metadata identify omitted source records and are not checksums for the portable files. `MANIFEST.json` is the integrity reference for this distribution.

The held-out test recovery Brier was approximately 0.031071 for the context model and 0.142516 for the same-data coarse model. Safety Brier and fixed-bin calibration did not improve. Online recovery-and-safety was 4/8 for reuse, fresh, and expert policies, and 3/8 for the coarse policy. These small descriptive results do not establish learning superiority, original C1/C2, or production qualification. All rejected/unknown and negative outcomes remain in the exported denominators.

All experiment-algorithm language-model token counts in this context study were zero because these paths use local numerical models. Development/review assistant sessions are outside that accounting. The historical v8 language-model experiments have their own separate accounting.
