#include <stdlib.h>

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
  if (!env_enabled("CODEX_TCMALLOC_ENABLE_BACKGROUND_RELEASE")) return;

  const char* rate = std::getenv("CODEX_TCMALLOC_BACKGROUND_RELEASE_RATE_BPS");
  if (rate != nullptr && rate[0] != '\0') {
    size_t bytes_per_second = std::strtoull(rate, nullptr, 10);
    tcmalloc::MallocExtension::SetBackgroundReleaseRate(
        static_cast<tcmalloc::MallocExtension::BytesPerSecond>(
            bytes_per_second));
  }

  std::thread([]() { tcmalloc::MallocExtension::ProcessBackgroundActions(); })
      .detach();
}

struct CodexWrapperInit {
  CodexWrapperInit() { maybe_start_background_release(); }
} init;

}  // namespace

extern "C" void tcmalloc_wrapper_touch(void) {
  void* p = malloc(1);
  free(p);
}
