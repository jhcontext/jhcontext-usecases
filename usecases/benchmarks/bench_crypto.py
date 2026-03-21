"""B6: Crypto overhead isolation — hash, sign, verify independently."""

from __future__ import annotations

import os

from jhcontext.crypto import compute_sha256, compute_content_hash, sign_envelope, verify_envelope
from jhcontext.canonicalize import canonicalize

from .config import ITERATIONS, WARMUP, CRYPTO_PAYLOAD_SIZES
from .helpers import timed, build_healthcare_envelope


def run() -> dict:
    print("  [B6] Crypto overhead...")

    results: dict = {}

    # SHA-256 at different payload sizes
    for size in CRYPTO_PAYLOAD_SIZES:
        data = os.urandom(size)
        label = f"sha256_{size}b"
        results[label] = timed(lambda d=data: compute_sha256(d), ITERATIONS * 2, WARMUP)

    # Canonicalize envelope
    env, _ = build_healthcare_envelope()
    env_dict = env.to_jsonld(include_proof=False)
    results["canonicalize"] = timed(lambda: canonicalize(env_dict), ITERATIONS * 2, WARMUP)

    # Content hash (canonicalize + SHA-256)
    results["content_hash"] = timed(lambda: compute_content_hash(env_dict), ITERATIONS * 2, WARMUP)

    # Sign envelope (Ed25519 key gen + sign)
    results["sign_envelope"] = timed(lambda: sign_envelope(env, "did:bench:signer"), ITERATIONS, WARMUP)

    # Verify envelope
    signed_env, _ = build_healthcare_envelope()  # already signed
    results["verify_envelope"] = timed(lambda: verify_envelope(signed_env), ITERATIONS, WARMUP)

    # PROV digest (serialize + SHA-256)
    _, prov = build_healthcare_envelope()
    results["prov_digest"] = timed(lambda: prov.digest(), ITERATIONS, WARMUP)

    return results
