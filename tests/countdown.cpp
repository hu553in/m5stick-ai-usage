#include "../firmware/src/quota_time.h"
#include <cassert>
#include <cstring>
#include <limits>

int main() {
  char value[COUNTDOWN_BUFFER_SIZE];
  const int64_t now = 1791302400;
  struct Case {
    int64_t reset;
    const char *expected;
  };
  const Case cases[] = {{0, "-"},
                        {now - 1, "0m"},
                        {now, "0m"},
                        {now + 1, "1m"},
                        {now + 59, "1m"},
                        {now + 60, "1m"},
                        {now + 3600, "1h0m"},
                        {now + 4800, "1h20m"},
                        {now + 3LL * 86400 + 5LL * 3600 + 20LL * 60, "3d5h20m"},
                        {now + 7LL * 86400, "7d0h0m"},
                        {std::numeric_limits<int64_t>::max(), "106751991146567d23h31m"}};
  for (auto test : cases) {
    formatCountdown(value, sizeof(value), test.reset, now);
    assert(std::strcmp(value, test.expected) == 0);
  }
  formatCountdown(value, sizeof(value), now + 60, 0);
  assert(std::strcmp(value, "-") == 0);
  formatCountdown(value, sizeof(value), std::numeric_limits<int64_t>::max(), 1);
  assert(std::strcmp(value, "106751991167300d15h31m") == 0);

  const int64_t maxTime = std::numeric_limits<int64_t>::max();
  const uint64_t maxElapsed = std::numeric_limits<uint64_t>::max();
  struct ClockCase {
    int64_t timestamp;
    uint64_t elapsedMillis;
    int64_t expected;
  };
  const ClockCase clockCases[] = {{now, 0, now},
                                  {now, 999, now},
                                  {now, 1000, now + 1},
                                  {now, 4294967296ULL, 1795597367},
                                  {maxTime - 2, 1999, maxTime - 1},
                                  {maxTime - 2, 2000, maxTime},
                                  {maxTime - 2, 3000, maxTime},
                                  {maxTime, 1000, maxTime},
                                  {maxTime, maxElapsed, maxTime},
                                  {1, maxElapsed, 18446744073709552LL},
                                  {0, maxElapsed, 0},
                                  {std::numeric_limits<int64_t>::min(), maxElapsed, 0}};
  for (const auto &test : clockCases) {
    const int64_t actual = advanceTimestamp(test.timestamp, test.elapsedMillis);
    assert(actual == test.expected);
  }
}
