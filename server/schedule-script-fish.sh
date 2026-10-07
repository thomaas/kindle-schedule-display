#!/bin/sh

# 0,30 6-20 * * 1-5,7 sh /home/pi/fishdraw/schedule-script-fish.sh

# Rotation for landscape display: use 90 or -90 depending on how the Kindle is placed
ROTATE=90
TARGET=/home/pi/fishdraw/schedule-script-output.png
CACHE_DIR=/home/pi/fishdraw/fish-cache
SCHEDULE_SCRIPT=/home/pi/kindle-weather-display/scheduleScript/schedule-script.py

cd "$(dirname "$0")"

# The fish is named after the lessons of the day, cancelled ones with "non"
# ("Nonmathematicus musicus biologicus"), so every timetable gets its own fish.
# Without lessons (holidays) fishdraw picks a random fish with a random name.
FISH_NAME=$(python3 "$SCHEDULE_SCRIPT" --fish-name --no-weather)

render() {
	rsvg-convert -z 3 -b white schedule-script-output.svg | magick png:- -flatten -rotate $ROTATE -resize 600x800 -gravity center -background white -extent 600x800 -colorspace Gray -dither FloydSteinberg -colors 16 -depth 8 -define png:color-type=0 $TARGET
}

if [ -n "$FISH_NAME" ]; then
	# Drawing a fish is slow, so finished images are cached per name (and rotation)
	# and reused on the next run with the same timetable.
	mkdir -p "$CACHE_DIR"
	CACHED="$CACHE_DIR/$(printf '%s' "$FISH_NAME" | md5sum | cut -d' ' -f1)-$ROTATE.png"
	if [ -s "$CACHED" ]; then
		cp "$CACHED" $TARGET
	else
		node fishdraw.js --seed "$FISH_NAME" > schedule-script-output.svg
		render && cp $TARGET "$CACHED"
	fi
else
	node fishdraw.js > schedule-script-output.svg
	render
fi
cp $TARGET /var/www/html/
