#include <stdlib.h>

extern "C" void tcmalloc_wrapper_touch(void) {
  void* p = malloc(1);
  free(p);
}
