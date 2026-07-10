#include <stdlib.h>

#include <cstdio>
#include <cstring>
#include <thread>

#include "tcmalloc/malloc_extension.h"

namespace {

bool env_enabled(const char* name) {
  const char* value = std::getenv(name);
  if (value == nullptr) return false;
  return std::strcmp(value, "1") == 0 || std::strcmp(value, "true") == 0 ||
         std::strcmp(value, "TRUE") == 0 || std::strcmp(value, "yes") == 0;
}

void maybe_start_background_release() {
  if (!env_enabled("TEMERAIRE_TCMALLOC_ENABLE_BACKGROUND_RELEASE")) return;

  const char* rate =
      std::getenv("TEMERAIRE_TCMALLOC_BACKGROUND_RELEASE_RATE_BPS");
  char* rate_end = nullptr;
  size_t bytes_per_second =
      rate == nullptr ? 0 : std::strtoull(rate, &rate_end, 10);
  if (rate == nullptr || rate[0] == '\0' || rate_end == nullptr ||
      rate_end[0] != '\0' || bytes_per_second == 0) {
    std::fprintf(stderr,
                 "temeraire-wrapper: release enabled without a positive rate\n");
    std::abort();
  }

  tcmalloc::MallocExtension::SetBackgroundReleaseRate(
      tcmalloc::MallocExtension::BytesPerSecond{bytes_per_second});

  std::fprintf(stderr,
               "temeraire-wrapper: background release enabled rate_bps=%zu\n",
               bytes_per_second);
  std::fflush(stderr);

  std::thread([]() {
    tcmalloc::MallocExtension::ProcessBackgroundActions();
  }).detach();
}

struct TemeraireWrapperInit {
  TemeraireWrapperInit() { maybe_start_background_release(); }
} init;

}  // namespace

extern "C" void tcmalloc_wrapper_touch(void) {
  void* p = malloc(1);
  free(p);
}
