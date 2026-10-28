# Reproducing an archived experiment

Recorded interpreter: `/Users/zhuyang/miniconda3/bin/python`. Python: `3.14.6 | packaged by Anaconda, Inc. | (main, Jul  9 2026, 14:30:20) [Clang 20.1.8 ]`.

1. Create a compatible isolated Python environment.
2. Install the recorded packages with `python -m pip install -r requirements.lock`.
3. Restore prepared data/model files and verify the SHA256 values listed in the run's inputs.
4. Set AIR_DATA_DIR and AIR_MODEL_DIR to those verified local directories.
5. Copy the archived run directory, including config.yaml, then enter its code/ directory.
   Execute run.json's command, changing the output directory to a new empty location.
   Preserve the configuration and random seed.

HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1 are required. CUDA hardware and a compatible driver
are required for CUDA configurations. A different platform may require compatible wheel versions;
the lock is an environment record, not a guarantee of bitwise reproducibility.

## Unavailable metadata

- GPU hardware/driver query unavailable: nvidia-smi did not return usable output.
