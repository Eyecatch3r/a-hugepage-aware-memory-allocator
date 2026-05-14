#include <stdlib.h>

#include <cstring>
#include <dlfcn.h>
#include <thread>

namespace {

using ProcessBackgroundActionsFn = void (*)();
using SetBackgroundReleaseRateFn = void (*)(size_t);

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
    void* rate_symbol = dlsym(
        RTLD_DEFAULT,
        "_ZN8tcmalloc15MallocExtension24SetBackgroundReleaseRateENS0_14BytesPerSecondE");
    if (rate_symbol != nullptr) {
      auto set_background_release_rate =
          reinterpret_cast<SetBackgroundReleaseRateFn>(rate_symbol);
      set_background_release_rate(bytes_per_second);
    }
  }

  // This symbol is not present in every public tcmalloc revision we benchmark.
  void* symbol = dlsym(
      RTLD_DEFAULT,
      "_ZN8tcmalloc15MallocExtension24ProcessBackgroundActionsEv");
  if (symbol == nullptr) return;

  auto process_background_actions =
      reinterpret_cast<ProcessBackgroundActionsFn>(symbol);
  std::thread([process_background_actions]() {
    process_background_actions();
  }).detach();
}

struct CodexWrapperInit {
  CodexWrapperInit() { maybe_start_background_release(); }
} init;

}  // namespace

extern "C" void tcmalloc_wrapper_touch(void) {
  void* p = malloc(1);
  free(p);
}
