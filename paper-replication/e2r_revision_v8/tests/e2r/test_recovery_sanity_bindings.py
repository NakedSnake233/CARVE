from pathlib import Path
import pytest

from scripts.e2r.run_recovery_sanity import (
    EXPECTED_BASELINE,EXPECTED_MANIFEST,validate_static_bindings,
)


def test_exact_v2_baseline_and_manifest_bindings_accept():
    result=validate_static_bindings(EXPECTED_BASELINE,EXPECTED_BASELINE.read_bytes(),
                                    EXPECTED_MANIFEST,EXPECTED_MANIFEST.read_bytes())
    assert result['baseline_path']==str(EXPECTED_BASELINE)
    assert result['manifest_path']==str(EXPECTED_MANIFEST)


def test_old_path_and_altered_bytes_reject(tmp_path):
    old=EXPECTED_BASELINE.parent.parent/'recovery_baseline_v1'/'baseline.json'
    with pytest.raises(ValueError,match='baseline path'):
        validate_static_bindings(old,old.read_bytes(),EXPECTED_MANIFEST,EXPECTED_MANIFEST.read_bytes())
    with pytest.raises(ValueError,match='baseline content'):
        validate_static_bindings(EXPECTED_BASELINE,EXPECTED_BASELINE.read_bytes()+b' ',
                                 EXPECTED_MANIFEST,EXPECTED_MANIFEST.read_bytes())
    with pytest.raises(ValueError,match='manifest content'):
        validate_static_bindings(EXPECTED_BASELINE,EXPECTED_BASELINE.read_bytes(),
                                 EXPECTED_MANIFEST,EXPECTED_MANIFEST.read_bytes()+b' ')
