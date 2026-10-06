#pragma once
#include <inttypes.h>
#include <stdio.h>

// Fits int64_t intervals and GCC 8's conservative snprintf bounds, including NUL.
constexpr size_t COUNTDOWN_BUFFER_SIZE = 32;

inline int64_t advanceTimestamp(int64_t timestamp, uint64_t elapsedMillis) {
  if (timestamp <= 0)
    return 0;
  const uint64_t elapsedSeconds = elapsedMillis / 1000;
  // A timestamp near the wire-format limit must not wrap into negative time.
  if (elapsedSeconds > static_cast<uint64_t>(INT64_MAX - timestamp))
    return INT64_MAX;
  return timestamp + static_cast<int64_t>(elapsedSeconds);
}

inline void formatCountdown(char *out, size_t capacity, int64_t reset, int64_t now) {
  if (reset <= 0 || now <= 0) {
    snprintf(out, capacity, "-");
    return;
  }
  // Round up so 59 remaining seconds do not look like an expired window.
  int64_t seconds = reset > now ? reset - now : 0;
  int64_t minutes = seconds / 60 + (seconds % 60 != 0);
  int64_t days = minutes / 1440;
  int hours = static_cast<int>((minutes / 60) % 24);
  int mins = static_cast<int>(minutes % 60);
  if (days > 0)
    snprintf(out, capacity, "%" PRId64 "d%dh%dm", days, hours, mins);
  else if (hours > 0)
    snprintf(out, capacity, "%dh%dm", hours, mins);
  else
    snprintf(out, capacity, "%dm", mins);
}
