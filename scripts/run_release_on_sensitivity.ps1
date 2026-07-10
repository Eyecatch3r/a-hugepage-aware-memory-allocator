$ErrorActionPreference = 'Stop'

docker compose run --rm `
  -e 'RELEASE_RATES_MIB=16,64,256' `
  -e 'RELEASE_SENSITIVITY_REPEATS=4' `
  temeraire-dev `
  bash -lc 'echo always > /sys/kernel/mm/transparent_hugepage/enabled && echo always > /sys/kernel/mm/transparent_hugepage/defrag && ./scripts/run_release_on_sensitivity.sh'

exit $LASTEXITCODE
