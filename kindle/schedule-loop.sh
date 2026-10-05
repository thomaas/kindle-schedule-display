#!/bin/sh

# Power-saving update loop for the schedule display (Kindle 4).
#
# Each cycle: WiFi on -> fetch image -> display if changed -> WiFi off ->
# set RTC wake alarm -> suspend to RAM. Between updates the Kindle sleeps.
#
# Maintenance mode: if STAY_AWAKE exists locally (/mnt/us/schedule/STAY_AWAKE)
# or on the server (STAY_URL), the loop exits with WiFi on and without
# suspending, so the Kindle stays reachable via SSH. Delete the file and run
# init-schedule.sh again to resume.

DIR=/mnt/us/schedule
IMAGE_URL=http://192.168.40.191/schedule-script-output.png
STAY_URL=http://192.168.40.191/STAY_AWAKE

# Local time zone (POSIX format, Germany)
export TZ=CET-1CEST,M3.5.0,M10.5.0/3

# School days (1=Mon .. 7=Sun), active hours and update interval
SCHOOL_DAYS="1 2 3 4 5 7"
DAY_START=6          # first update at DAY_START:WAKE_MINUTE
DAY_END=20           # no further updates from this hour on
INTERVAL=1800        # seconds between updates during active hours
WAKE_MINUTE=5        # a few minutes after the Pi cron job

SUSPEND=1            # 1 = suspend to RAM between updates, 0 = plain sleep (WiFi still off)
WIFI_TIMEOUT=45      # seconds to wait for a WiFi connection
MAX_FAILURES=3       # show error image after this many failed fetches in a row
LOW_BATTERY=20       # show a charging hint below this battery level (percent)
EINK_SETTLE=5        # seconds to let the e-ink refresh finish before suspending

LOG=$DIR/schedule.log
LAST=$DIR/last.png
NEW=/tmp/schedule-new.png

cd "$DIR" || exit 1

# Keep running when the SSH session that started us is closed
trap '' HUP

log() {
	echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
	# Keep the log small
	if [ "$(wc -c < "$LOG")" -gt 100000 ]; then
		tail -n 200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
	fi
}

wifi_on() {
	lipc-set-prop com.lab126.wifid enable 1
	i=0
	while [ $i -lt $WIFI_TIMEOUT ]; do
		if [ "$(lipc-get-prop com.lab126.wifid cmState 2>/dev/null)" = "CONNECTED" ]; then
			# Give DHCP/DNS a moment
			sleep 2
			return 0
		fi
		sleep 1
		i=$((i + 1))
	done
	return 1
}

wifi_off() {
	lipc-set-prop com.lab126.wifid enable 0
}

stay_awake_requested() {
	[ -e "$DIR/STAY_AWAKE" ] && return 0
	wget -q -O /dev/null "$STAY_URL" 2>/dev/null
}

enter_maintenance() {
	log "maintenance mode, staying awake"
	wifi_on
	eips 1 1 "Wartungsmodus: SSH aktiv"
	exit 0
}

is_school_day() {
	for d in $SCHOOL_DAYS; do
		[ "$d" = "$1" ] && return 0
	done
	return 1
}

# Seconds until the next update
seconds_to_next_update() {
	dow=$(date +%u)
	h=$(date +%H); h=${h#0}
	m=$(date +%M); m=${m#0}
	s=$(date +%S); s=${s#0}
	now=$((h * 3600 + m * 60 + s))
	first=$((DAY_START * 3600 + WAKE_MINUTE * 60))

	if is_school_day "$dow"; then
		if [ $now -lt $first ]; then
			echo $((first - now))
			return
		fi
		if [ $((now + INTERVAL)) -lt $((DAY_END * 3600)) ]; then
			echo $INTERVAL
			return
		fi
	fi

	# Sleep until the first update of the next school day
	days=1
	next=$(( dow % 7 + 1 ))
	while ! is_school_day "$next" && [ $days -lt 7 ]; do
		days=$((days + 1))
		next=$(( next % 7 + 1 ))
	done
	echo $((days * 86400 - now + first))
}

# Full refresh: clear first (flash) and let it finish, then draw the image
show_image() {
	eips -c
	sleep 1
	eips -g "$1"
}

update_display() {
	rm -f "$NEW"
	if wget -q -O "$NEW" "$IMAGE_URL" && [ -s "$NEW" ]; then
		failures=0
		if [ -e "$LAST" ] && cmp -s "$NEW" "$LAST"; then
			log "image unchanged"
		else
			show_image "$NEW"
			mv "$NEW" "$LAST"
			log "image updated"
		fi
	else
		failures=$((failures + 1))
		log "fetch failed ($failures)"
		if [ $failures -ge $MAX_FAILURES ]; then
			show_image "$DIR/weather-image-error.png"
			# Force a redraw once the server is reachable again
			rm -f "$LAST"
		fi
	fi
}

# Overlay a charging hint at the bottom right when the battery is low
show_battery_warning() {
	batt=$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null)
	charging=$(lipc-get-prop com.lab126.powerd isCharging 2>/dev/null)
	[ -z "$batt" ] && return
	if [ "$batt" -lt $LOW_BATTERY ] && [ "$charging" != "1" ]; then
		msg="Akku ${batt}% - bitte laden"
		eips $((50 - ${#msg})) 39 "$msg"
		battery_warning=1
		log "low battery: $batt%"
	elif [ "$battery_warning" = "1" ]; then
		# Remove the hint again by redrawing the current image
		[ -e "$LAST" ] && show_image "$LAST"
		battery_warning=0
	fi
}

# Program RTC wake alarm and suspend to RAM
suspend_for() {
	if [ "$SUSPEND" != "1" ]; then
		log "sleeping for $1 s"
		sleep "$1"
		return
	fi
	rtc=""
	for r in /sys/class/rtc/rtc1 /sys/class/rtc/rtc0; do
		if [ -e "$r/wakealarm" ]; then
			rtc=$r
			break
		fi
	done
	if [ -z "$rtc" ]; then
		log "no RTC wakealarm found, falling back to sleep"
		sleep "$1"
		return
	fi
	echo 0 > "$rtc/wakealarm"
	echo $(( $(cat "$rtc/since_epoch") + $1 )) > "$rtc/wakealarm"
	# eips returns before the panel has finished its refresh; suspending
	# too early leaves a faint, half-drawn image
	sync
	sleep "$EINK_SETTLE"
	log "suspending for $1 s"
	echo mem > /sys/power/state
	# Execution continues here after wake-up
	log "woke up, rtc wakealarm now: $(cat "$rtc/wakealarm")"
}

failures=0
battery_warning=0
log "loop started"
# Always draw on the first cycle, even if the image has not changed
rm -f "$LAST"

while true; do
	if wifi_on; then
		stay_awake_requested && enter_maintenance
		update_display
	else
		failures=$((failures + 1))
		log "no WiFi connection ($failures)"
		[ -e "$DIR/STAY_AWAKE" ] && enter_maintenance
	fi

	show_battery_warning
	wifi_off
	suspend_for "$(seconds_to_next_update)"
done
