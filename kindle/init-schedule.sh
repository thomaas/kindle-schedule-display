#!/bin/sh

# Stop the Kindle UI and power daemon, then run the update loop in the background.
# The loop handles sleeping itself (RTC wake alarm + suspend to RAM).

/etc/init.d/framework stop
/etc/init.d/powerd stop

# No nohup on the Kindle; the loop ignores SIGHUP itself
/mnt/us/schedule/schedule-loop.sh < /dev/null > /dev/null 2>&1 &
