#pragma once
#include <stdint.h>
#include <stdio.h>

inline void formatCountdown(char *out, size_t capacity, int64_t reset,
                            int64_t now) {
  if (reset <= 0 || now <= 0) {
    snprintf(out, capacity, "-");
    return;
  }
  // Round up so 59 remaining seconds do not look like an expired window.
  int64_t minutes = reset > now ? (reset - now + 59) / 60 : 0;
  int days = minutes / 1440;
  int hours = (minutes / 60) % 24;
  int mins = minutes % 60;
  if (days > 0)
    snprintf(out, capacity, "%dd%dh%dm", days, hours, mins);
  else if (hours > 0)
    snprintf(out, capacity, "%dh%dm", hours, mins);
  else
    snprintf(out, capacity, "%dm", mins);
}
