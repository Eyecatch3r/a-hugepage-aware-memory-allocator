# Result archives

These hold the per-trial CSV files for every run. Git does not carry them,
because there are more than 400,000. They travel with the submitted artifact.

| Archive | Contents |
|---|---|
| `temeraire-correction-runs-202608.tar.gz` | Correction run, August 2026. Four sequential pairs and four EVAL pairs, with CPU time. Holds the reported result. |
| `temeraire-node85-results.tar.gz` | First bare-metal run, July 2026. Four balanced pairs at 16 MiB/s. |
| `temeraire-node85-results-with-sensitivity.tar.gz` | The same run plus four pairs at 64 MiB/s and four at 256 MiB/s. |
| `temeraire-wsl-docker-results.tar.gz` | Docker/WSL development runs. Superseded. |

Check all four:

```bash
cd archives && sha256sum -c SHA256SUMS.txt
```

Unpack one over the matching result tree:

```bash
tar xzf archives/temeraire-correction-runs-202608.tar.gz -C results/node85-rerun --strip-components=1
```

You do not need these to check the reported numbers. See "Verify the Reported
Numbers from a Clone" in the top-level README.
