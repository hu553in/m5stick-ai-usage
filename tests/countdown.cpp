#include "../firmware/src/quota_time.h"
#include <cassert>
#include <cstring>

int main() {
  char value[24];
  const int64_t now = 1791302400;
  struct Case {
    int64_t reset;
    const char *expected;
  };
  Case cases[] = {{0, "-"},
                  {now - 1, "0m"},
                  {now, "0m"},
                  {now + 1, "1m"},
                  {now + 59, "1m"},
                  {now + 60, "1m"},
                  {now + 3600, "1h0m"},
                  {now + 4800, "1h20m"},
                  {now + 3 * 86400 + 5 * 3600 + 20 * 60, "3d5h20m"},
                  {now + 7 * 86400, "7d0h0m"}};
  for (auto test : cases) {
    formatCountdown(value, sizeof(value), test.reset, now);
    assert(std::strcmp(value, test.expected) == 0);
  }
  formatCountdown(value, sizeof(value), now + 60, 0);
  assert(std::strcmp(value, "-") == 0);
}
